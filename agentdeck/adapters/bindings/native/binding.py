"""``Native.http()``: the AgentDeck protocol, the SPI's own reference implementation
(``docs/design/protocols/native-wire.md``). Frames are ``Event.model_dump_json()`` verbatim,
nothing reshaped (``rulings.md`` 18); every route reaches a Deck only through
:class:`~agentdeck.bindings.ProtocolGateway` or a public ``Run`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agentdeck.bindings import (
    PROTOCOL_SPI_VERSION,
    BindingInfo,
    GatewayError,
    GatewayFailureCode,
    HttpEndpoint,
    ProtocolGateway,
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
    """``kind="protocol"``: one Starlette app over ten routes, all thin (parse, call
    gateway/Run, map errors). No route builds its own response body reshaping an
    :class:`~agentdeck.core.events.Event`  -  the wire *is* the canonical event.
    """

    def __init__(self, *, path: str = "/", namespace: str | None = None) -> None:
        self.info = BindingInfo(
            name="native",
            kind="protocol",
            transport="http",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
            # Native reshapes nothing, so it genuinely forwards every kind the schema has, not
            # only the ones behind its own capability tags.
            projects=_ADVERTISES | KNOWN_KINDS,
        )
        self._path = path
        self._namespace = namespace
        self._gateway: ProtocolGateway | None = None

    def _require_gateway(self) -> ProtocolGateway:
        """Every route runs after :meth:`build`, per the `Binding` contract (Exposure calls it
        before serving anything); this is what makes that fact visible to the type checker."""
        assert self._gateway is not None, "build() must run before a route is served"
        return self._gateway

    def build(self, gateway: ProtocolGateway) -> HttpEndpoint:
        """Pure: stores the gateway, wires the routes, opens nothing."""
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
        """Nothing to prewarm: every route reaches the gateway lazily, per request."""

    async def stop(self) -> None:
        """No background work this binding owns."""

    async def _list_targets(self, request: Request) -> JSONResponse:
        targets = self._require_gateway().targets()
        return JSONResponse(
            [
                {"name": t.name, "kind": t.kind, "description": t.description, "input_schema": t.input_schema}
                for t in targets
            ]
        )

    async def _start_run(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
            run = await self._require_gateway().start(
                body["target"],
                body["input"],
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
            raw_limit = request.query_params.get("limit")
            limit = int(raw_limit) if raw_limit is not None else None
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
            body = await request.json()
            run = await self._require_gateway().get_run(request.path_params["run_id"], namespace=self._namespace)
            await run.answer(body["value"])
        except Exception as exc:
            return _error_response(exc)
        return JSONResponse({}, status_code=200)

    async def _events(self, request: Request) -> Any:
        """SSE tail of one run's canonical events, ``from_seq`` on. The first event is pulled
        before the response is built, so a refusal (``NOT_FOUND``) is still a status code and
        not a stream that opens and immediately stops (``surfaces/serve/app.py``'s own reason).
        """
        try:
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


def _parse_status(raw: str | None) -> RunStatus | None:
    if raw is None:
        return None
    try:
        return RunStatus(raw)
    except ValueError:
        raise ValueError(f"status must be one of {sorted(s.value for s in RunStatus)}, got {raw!r}") from None


async def _json_body(request: Request) -> dict[str, Any]:
    return await request.json() if await request.body() else {}


def _from_seq(request: Request) -> int:
    """``Last-Event-ID`` resumes from the id after the last one the client saw (the standard SSE
    reading of that header); ``?from_seq=`` is a caller-computed starting point, taken verbatim.
    """
    last_event_id = request.headers.get("last-event-id")
    if last_event_id is not None:
        return int(last_event_id) + 1
    return int(request.query_params.get("from_seq", "0"))


async def _first(stream: Any) -> Any:
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return _END


def _frame(event: Any) -> str:
    return f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, GatewayError):
        code, message = exc.code, exc.message
    elif isinstance(exc, RunStateError):
        code, message = GatewayFailureCode.CONFLICT, str(exc)
    elif isinstance(exc, UnsupportedControlError):
        code, message = GatewayFailureCode.UNSUPPORTED, str(exc)
    elif isinstance(exc, KeyError):
        code, message = GatewayFailureCode.INVALID_INPUT, f"missing field: {exc.args[0]!r}"
    elif isinstance(exc, (TypeError, ValueError)):
        code, message = GatewayFailureCode.INVALID_INPUT, str(exc)
    else:
        code, message = GatewayFailureCode.INTERNAL, _INTERNAL_MESSAGE
    return JSONResponse({"detail": message}, status_code=_STATUS[code])


__all__ = ["Native", "NativeBinding"]
