"""AgentDeck's native HTTP protocol binding, reached as ``agentdeck.bindings.native.Native``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from agentdeck import RunStatus
from agentdeck.bindings import (
    PROTOCOL_SPI_VERSION,
    BindingInfo,
    DeckGateway,
    GatewayError,
    GatewayFailureCode,
    HttpEndpoint,
)
from agentdeck.errors import InputError, RunStateError, UnsupportedControlError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.requests import Request

    from agentdeck import Event, Run
    from agentdeck.bindings import Binding

logger = logging.getLogger(__name__)

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


class _InvalidRequestError(Exception):
    """A malformed request this binding rejects, never a gateway or ``Run`` failure."""


class _NativeBinding:
    """``kind="protocol"``: routes over ``DeckGateway``/``Run``, nothing reshaped."""

    def __init__(self, *, path: str = "/", namespace: str | None = None, name: str = "native") -> None:
        self.info = BindingInfo(
            name=name,
            kind="protocol",
            transport="http",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
        )
        self._path = path
        self._namespace = namespace
        self._gateway: DeckGateway | None = None

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
            ],
            # One boundary for every route: a new route cannot forget to translate its failures.
            exception_handlers=_HANDLERS,
        )
        return HttpEndpoint(path=self._path, app=app)

    async def start(self) -> None:
        """No background work."""

    async def stop(self) -> None:
        """No background work."""

    @property
    def _deck(self) -> DeckGateway:
        if self._gateway is None:
            # A 500 either way, but the reason stays in the log rather than being sanitized
            # away by GatewayError's own INTERNAL rule.
            raise RuntimeError("build() has not run: this binding has no gateway")
        return self._gateway

    async def _run_of(self, request: Request) -> Run:
        return await self._deck.get_run(request.path_params["run_id"], namespace=self._namespace)

    async def _list_targets(self, request: Request) -> JSONResponse:
        return JSONResponse(
            [
                {"name": t.name, "kind": t.kind, "description": t.description, "input_schema": t.input_schema}
                for t in self._deck.targets()
            ]
        )

    async def _start_run(self, request: Request) -> JSONResponse:
        body = await _json_body(request)
        run = await self._deck.start(
            _require(body, "target"),
            _require(body, "input"),
            session_id=body.get("session_id"),
            key=body.get("key"),
            namespace=self._namespace,
        )
        return JSONResponse(await _run_summary(run), status_code=201)

    async def _list_runs(self, request: Request) -> JSONResponse:
        runs = await self._deck.list_runs(
            namespace=self._namespace,
            status=_parse_status(request.query_params.get("status")),
            limit=_parse_non_negative(request.query_params.get("limit"), "limit"),
        )
        return JSONResponse([await _run_summary(run) for run in runs])

    async def _get_run(self, request: Request) -> JSONResponse:
        return JSONResponse(await _run_summary(await self._run_of(request)))

    async def _cancel(self, request: Request) -> JSONResponse:
        run = await self._run_of(request)
        await run.cancel((await _json_body(request)).get("reason"))
        return JSONResponse({})

    async def _pause(self, request: Request) -> JSONResponse:
        run = await self._run_of(request)
        await run.pause((await _json_body(request)).get("reason"))
        return JSONResponse({})

    async def _resume(self, request: Request) -> JSONResponse:
        await (await self._run_of(request)).resume()
        return JSONResponse({})

    async def _pending(self, request: Request) -> JSONResponse:
        return JSONResponse(await (await self._run_of(request)).pending())

    async def _answer(self, request: Request) -> JSONResponse:
        body = await _json_body(request)
        await (await self._run_of(request)).answer(_require(body, "value"))
        return JSONResponse({})

    async def _events(self, request: Request) -> Response:
        """Stream canonical run events as SSE."""
        # The first event is pulled before the stream opens, so a lookup or follow failure is
        # still an HTTP error rather than a half-sent body.
        run = await self._run_of(request)
        stream = run.events(from_seq=_from_seq(request), follow=True)
        opening = await _first(stream)

        async def frames() -> AsyncIterator[str]:
            if opening is not None:
                yield _frame(opening)
                async for event in stream:
                    yield _frame(event)

        return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class Native:
    """The AgentDeck protocol: the one (path, transport) pair it supports
    (``docs/design/protocols/bindings.md``)."""

    @staticmethod
    def http(path: str = "/", *, namespace: str | None = None, name: str = "native") -> Binding:
        """One binding per (path, namespace). `name` distinguishes a second one in the same
        exposure, which `expose()` requires (`exposure.md`)."""
        return _NativeBinding(path=path, namespace=namespace, name=name)


async def _run_summary(run: Run) -> dict[str, Any]:
    can = run.can
    return {
        "run_id": run.id,
        "namespace": run.namespace,
        "session_id": run.session_id,
        "status": await run.status(),
        "can": {"pause": can.pause, "resume": can.resume, "cancel": can.cancel},
    }


def _require(body: dict[str, Any], field: str) -> Any:
    try:
        return body[field]
    except KeyError:
        raise _InvalidRequestError(f"missing field: {field!r}") from None


def _parse_status(raw: str | None) -> RunStatus | None:
    if raw is None:
        return None
    try:
        return RunStatus(raw)
    except ValueError:
        raise _InvalidRequestError(f"status must be one of {sorted(s.value for s in RunStatus)}, got {raw!r}") from None


def _parse_non_negative(raw: str | None, field: str) -> int | None:
    """Validated here, not by whatever store the deck happens to hold."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise _InvalidRequestError(f"{field} must be an integer, got {raw!r}") from None
    if value < 0:
        raise _InvalidRequestError(f"{field} must not be negative, got {value}")
    return value


async def _json_body(request: Request) -> dict[str, Any]:
    if not await request.body():
        return {}
    try:
        body = await request.json()
    except ValueError as exc:
        raise _InvalidRequestError(f"malformed JSON body: {exc}") from None
    if not isinstance(body, dict):
        raise _InvalidRequestError("JSON body must be an object")
    return body


def _from_seq(request: Request) -> int:
    """The replay cursor, from Last-Event-ID or from_seq."""
    last_event_id = request.headers.get("last-event-id")
    raw = last_event_id if last_event_id is not None else request.query_params.get("from_seq", "0")
    seq = _parse_non_negative(raw, "last-event-id" if last_event_id is not None else "from_seq")
    assert seq is not None  # raw is never None here: the query param defaults to "0"
    return seq + 1 if last_event_id is not None else seq


async def _first(stream: AsyncIterator[Event]) -> Event | None:
    """The segment's first event, or None if it ended before producing one."""
    try:
        return await anext(stream)
    except StopAsyncIteration:
        return None


def _frame(event: Event) -> str:
    return f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"


def _detail(message: str, code: GatewayFailureCode) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=_STATUS[code])


async def _on_gateway_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GatewayError)
    return _detail(exc.message, exc.code)


async def _on_bad_request(request: Request, exc: Exception) -> JSONResponse:
    return _detail(str(exc), GatewayFailureCode.INVALID_INPUT)


async def _on_run_state(request: Request, exc: Exception) -> JSONResponse:
    return _detail(str(exc), GatewayFailureCode.CONFLICT)


async def _on_unsupported(request: Request, exc: Exception) -> JSONResponse:
    return _detail(str(exc), GatewayFailureCode.UNSUPPORTED)


async def _on_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """Logged as well as re-raised: whether an ASGI server logs a handled exception is its own
    business, and ``serve.py`` sets the precedent that the reason is never only on the wire.
    """
    logger.exception("%s serving %s", type(exc).__name__, request.url.path, exc_info=exc)
    return _detail(_INTERNAL_MESSAGE, GatewayFailureCode.INTERNAL)


_HANDLERS: dict[Any, Any] = {
    GatewayError: _on_gateway_error,
    _InvalidRequestError: _on_bad_request,
    InputError: _on_bad_request,
    RunStateError: _on_run_state,
    UnsupportedControlError: _on_unsupported,
    Exception: _on_unexpected,
}

__all__ = ["Native"]
