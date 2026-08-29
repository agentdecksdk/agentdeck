"""AgentDeck's native HTTP protocol binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agentdeck.bindings import (
    PROTOCOL_SPI_VERSION,
    BindingInfo,
    DeckGateway,
    GatewayError,
    GatewayFailureCode,
    HttpEndpoint,
)
from agentdeck.core.events import KNOWN_KINDS
from agentdeck.core.status import RunStatus
from agentdeck.errors import RunStateError, UnsupportedControlError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.requests import Request

_ADVERTISES = frozenset({"streaming", "text", "hitl", "control.cancel", "control.pause", "control.resume"})

_STATUS: dict[GatewayFailureCode, int] = {
    GatewayFailureCode.NOT_FOUND: 404,
    GatewayFailureCode.BUSY: 409,
    GatewayFailureCode.CONFLICT: 409,
    GatewayFailureCode.INVALID_INPUT: 422,
    GatewayFailureCode.UNSUPPORTED: 501,
    GatewayFailureCode.INTERNAL: 500,
}
_INTERNAL_MESSAGE = "internal error"

# What anext() returns at the end of a segment: distinct from "the first event happens to be
# None", which no canonical event ever is.
_END = object()


class NativeBinding:
    """``kind="protocol"``: routes over ``DeckGateway``/``Run``, nothing reshaped."""

    def __init__(self, *, path: str = "/", namespace: str | None = None) -> None:
        self.info = BindingInfo(
            name="native",
            kind="protocol",
            transport="http",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
            projects=KNOWN_KINDS,
        )
        self._path = path
        self._namespace = namespace
        self._gateway: DeckGateway | None = None

    def _require_gateway(self) -> DeckGateway:
        """Return the gateway after the binding has been built."""
        assert self._gateway is not None, "build() must run before a route is served"
        return self._gateway

    def build(self, gateway: DeckGateway) -> HttpEndpoint:
        self._gateway = gateway
        app = Starlette(
            routes=[
                Route("/targets", self._list_targets, methods=["GET"]),
                Route("/runs", self._start_run, methods=["POST"]),
                Route("/runs", self._list_runs, methods=["GET"]),
                Route("/runs/{run_id}", self._get_run, methods=["GET"]),
                Route("/runs/{run_id}/events", self._events, methods=["GET"]),
                Route("/runs/{run_id}/cancel", self._cancel, methods=["POST"]),
                Route("/runs/{run_id}/pause", self._pause, methods=["POST"]),
                Route("/runs/{run_id}/resume", self._resume, methods=["POST"]),
                Route("/runs/{run_id}/pending", self._pending, methods=["GET"]),
                Route("/runs/{run_id}/answer", self._answer, methods=["POST"]),
            ]
        )
        return HttpEndpoint(path=self._path, app=app)

    async def start(self) -> None:
        """No background work."""

    async def stop(self) -> None:
        """No background work."""

    async def _list_targets(self, request: Request) -> JSONResponse:
        try:
            targets = self._require_gateway().targets()
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse(
            [
                {"name": t.name, "kind": t.kind, "description": t.description, "input_schema": t.input_schema}
                for t in targets
            ]
        )

    async def _start_run(self, request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            run = await self._require_gateway().start(
                _require(body, "target"),
                _require(body, "input"),
                session_id=body.get("session_id"),
                key=body.get("key"),
                namespace=self._namespace,
            )
            summary = await _run_summary(run)
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse(summary, status_code=201)

    async def _list_runs(self, request: Request) -> JSONResponse:
        try:
            status = _parse_status(request.query_params.get("status"))
            limit = _parse_limit(request.query_params.get("limit"))
            runs = await self._require_gateway().list_runs(namespace=self._namespace, status=status, limit=limit)
            summaries = [await _run_summary(run) for run in runs]
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse(summaries)

    async def _get_run(self, request: Request) -> JSONResponse:
        try:
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            summary = await _run_summary(run)
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse(summary)

    async def _cancel(self, request: Request) -> JSONResponse:
        return await self._control(request, "cancel")

    async def _pause(self, request: Request) -> JSONResponse:
        return await self._control(request, "pause")

    async def _resume(self, request: Request) -> JSONResponse:
        return await self._control(request, "resume")

    async def _control(self, request: Request, verb: str) -> JSONResponse:
        try:
            body = await _json_body(request)
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            if verb == "cancel":
                await run.cancel(body.get("reason"))
            elif verb == "pause":
                await run.pause(body.get("reason"))
            else:
                await run.resume()
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse({}, status_code=200)

    async def _pending(self, request: Request) -> JSONResponse:
        try:
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            result = await run.pending()
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse(result)

    async def _answer(self, request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            await run.answer(_require(body, "value"))
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse({}, status_code=200)

    async def _events(self, request: Request) -> Any:
        """Stream canonical run events as SSE."""
        try:
            # Pull the first event before opening the stream so lookup and follow failures stay
            # HTTP errors.
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            stream = run.events(from_seq=_from_seq(request), follow=True)
            opening = await _first(stream)
        except Exception as exc:
            return _error_response(exc)

        async def frames() -> AsyncIterator[str]:
            if opening is not _END:
                yield _frame(opening)
                async for event in stream:
                    yield _frame(event)

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class Native:
    """Factory for the AgentDeck-native binding: the one (path, transport) pair it supports
    (``docs/design/protocols/bindings.md``)."""

    @staticmethod
    def http(path: str = "/", *, namespace: str | None = None) -> NativeBinding:
        return NativeBinding(path=path, namespace=namespace)


async def _run_summary(run: Any) -> dict[str, Any]:
    status = await run.status()
    can = run.can
    return {
        "run_id": run.id,
        "namespace": run.namespace,
        "session_id": run.session_id,
        "status": status,
        "can": {"pause": can.pause, "resume": can.resume, "cancel": can.cancel},
    }


def _require(body: dict[str, Any], field: str) -> Any:
    try:
        return body[field]
    except KeyError:
        raise InvalidRequestError(f"missing field: {field!r}") from None


def _parse_status(raw: str | None) -> RunStatus | None:
    if raw is None:
        return None
    try:
        return RunStatus(raw)
    except ValueError:
        raise InvalidRequestError(f"status must be one of {sorted(s.value for s in RunStatus)}, got {raw!r}") from None


def _parse_limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise InvalidRequestError(f"limit must be an integer, got {raw!r}") from None


async def _json_body(request: Request) -> dict[str, Any]:
    if not await request.body():
        return {}
    try:
        return await request.json()
    except ValueError as exc:
        raise InvalidRequestError(f"malformed JSON body: {exc}") from None


def _from_seq(request: Request) -> int:
    """Resolve the replay cursor from Last-Event-ID or from_seq."""
    last_event_id = request.headers.get("last-event-id")
    raw = last_event_id if last_event_id is not None else request.query_params.get("from_seq", "0")
    try:
        seq = int(raw)
    except ValueError:
        raise InvalidRequestError(f"from_seq must be an integer, got {raw!r}") from None
    return seq + 1 if last_event_id is not None else seq


async def _first(stream: Any) -> Any:
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return _END


def _frame(event: Any) -> str:
    return f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"


class InvalidRequestError(Exception):
    """Malformed input this binding itself rejects, never a gateway or ``Run`` failure."""


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, GatewayError):
        code, message = exc.code, exc.message
    elif isinstance(exc, RunStateError):
        code, message = GatewayFailureCode.CONFLICT, str(exc)
    elif isinstance(exc, UnsupportedControlError):
        code, message = GatewayFailureCode.UNSUPPORTED, str(exc)
    elif isinstance(exc, InvalidRequestError):
        code, message = GatewayFailureCode.INVALID_INPUT, str(exc)
    else:
        code, message = GatewayFailureCode.INTERNAL, _INTERNAL_MESSAGE
    return JSONResponse({"detail": message}, status_code=_STATUS[code])


__all__ = ["Native", "NativeBinding"]
