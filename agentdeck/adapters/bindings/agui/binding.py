"""AgentDeck's AG-UI protocol binding, reached as ``agentdeck.bindings.agui.AGUI``
(``docs/design/protocols/agui.md``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ag_ui.core import RunAgentInput, RunErrorEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from pydantic import ValidationError as PydanticValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from agentdeck import RunStatus
from agentdeck.adapters.bindings.agui.adapter import (
    AdapterState,
    to_agentdeck_input,
    to_agentdeck_resume,
    to_agui_event,
)
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

    from ag_ui.core import ResumeEntry
    from starlette.requests import Request

    from agentdeck import Event, Run
    from agentdeck.bindings import Binding

logger = logging.getLogger(__name__)

_ADVERTISES = frozenset({"streaming", "text", "reasoning", "hitl", "multimodal-input", "control.cancel"})

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
    """A malformed or unsupported AG-UI request this binding rejects before the stream opens,
    never a gateway or ``Run`` failure."""


class _AGUIBinding:
    """``kind="protocol"``: one route, AG-UI's official models in, AG-UI's official events out."""

    def __init__(
        self, *, path: str = "/agui", target: str | None = None, namespace: str | None = None, name: str = "agui"
    ) -> None:
        self.info = BindingInfo(
            name=name,
            kind="protocol",
            transport="http",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
        )
        self._path = path
        self._target = target
        self._namespace = namespace
        self._gateway: DeckGateway | None = None

    def build(self, gateway: DeckGateway) -> HttpEndpoint:
        self._gateway = gateway
        app = Starlette(
            routes=[Route("/", self._handle, methods=["POST"])],
            # Pre-stream failures only; once the stream opens, `_handle` maps errors to RUN_ERROR itself.
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
            raise RuntimeError("build() has not run: this binding has no gateway")
        return self._gateway

    def _resolve_target(self, run_input: RunAgentInput) -> str:
        requested = _extension_target(run_input.forwarded_props)
        if self._target is not None:
            if requested is not None and requested != self._target:
                raise _InvalidRequestError(
                    f"this endpoint is pinned to target {self._target!r}; forwardedProps.agentdeck.target "
                    f"named {requested!r} instead."
                )
            return self._target
        names = sorted(t.name for t in self._deck.targets())
        if requested is not None:
            if requested not in names:
                raise _InvalidRequestError(f"no target named {requested!r}. Available: {names}")
            return requested
        if len(names) == 1:
            return names[0]
        raise _InvalidRequestError(
            f"this endpoint serves {len(names)} targets, so a request must name one in "
            f"forwardedProps.agentdeck.target. Available: {names}"
        )

    async def _find_waiting_run(self, session_id: str) -> Run:
        runs = await self._deck.list_runs(status=RunStatus.WAITING_ANSWER, namespace=self._namespace)
        matches = [run for run in runs if run.session_id == session_id]
        if not matches:
            raise _InvalidRequestError(f"no run is waiting for an answer on thread {session_id!r}")
        if len(matches) > 1:
            # A session has one active owner (agui.md); more than one match means two bindings
            # or namespaces share a thread id, which this binding cannot disambiguate.
            raise _InvalidRequestError(f"thread {session_id!r} matches more than one waiting run")
        return matches[0]

    async def _handle(self, request: Request) -> Response:
        run_input = await _parse_body(request)
        _reject_unsupported_fields(run_input)

        if run_input.resume:
            entry = _single_resume_entry(run_input.resume)
            run = await self._find_waiting_run(run_input.thread_id)
            interrupt = await _last_interrupt(run)
            # `event.payload`'s static type is the whole KnownPayload union; narrowing it to
            # RunInterrupted needs the class itself, which the SPI import boundary forbids.
            waiting_on = interrupt.payload.interrupt_id  # ty: ignore[unresolved-attribute]
            if waiting_on != entry.interrupt_id:
                raise _InvalidRequestError(
                    f"resume names interruptId {entry.interrupt_id!r}, but run {run.id!r} is waiting on {waiting_on!r}"
                )
            from_seq = interrupt.seq + 1
            action_run: Run | None = run
            target = None
            input_value: Any = to_agentdeck_resume(run_input)
        else:
            target = self._resolve_target(run_input)
            input_value = to_agentdeck_input(run_input)
            from_seq = 0
            action_run = None

        encoder = EventEncoder(accept=request.headers.get("accept", ""))
        state = AdapterState(thread_id=run_input.thread_id, run_id=run_input.run_id)

        async def frames() -> AsyncIterator[str]:
            nonlocal action_run
            yield encoder.encode(RunStartedEvent(thread_id=run_input.thread_id, run_id=run_input.run_id))
            try:
                if action_run is None:
                    assert target is not None, "the start path always resolves a target"
                    action_run = await self._deck.start(
                        target, input_value, session_id=run_input.thread_id, namespace=self._namespace
                    )
                else:
                    await action_run.answer(input_value)
                async for event in action_run.events(from_seq=from_seq, follow=True):
                    for ag_ui_event in to_agui_event(event, state):
                        yield encoder.encode(ag_ui_event)
            except asyncio.CancelledError:
                if action_run is not None:
                    await action_run.cancel("client disconnected")  # ruling 46
                raise
            except GatewayError as exc:
                yield encoder.encode(RunErrorEvent(message=exc.message, code=exc.code.name.lower()))
            except (InputError, RunStateError, UnsupportedControlError) as exc:
                yield encoder.encode(RunErrorEvent(message=str(exc), code="error"))
            except Exception as exc:
                logger.exception("%s serving %s", type(exc).__name__, request.url.path)
                yield encoder.encode(RunErrorEvent(message=_INTERNAL_MESSAGE, code="internal"))

        return StreamingResponse(frames(), media_type=encoder.get_content_type(), headers={"Cache-Control": "no-cache"})


class AGUI:
    """The AG-UI protocol: one endpoint per (path, namespace), full-Deck or pinned to one
    target (``docs/design/protocols/bindings.md``, ``agui.md``)."""

    @staticmethod
    def http(
        path: str = "/agui", *, target: str | None = None, namespace: str | None = None, name: str = "agui"
    ) -> Binding:
        """``target=None`` serves the whole Deck, routed by ``forwardedProps.agentdeck.target``;
        ``target="Support"`` pins the endpoint, plain AG-UI to any client. ``name`` distinguishes
        a second instance in the same exposure, which ``expose()`` requires (``exposure.md``)."""
        return _AGUIBinding(path=path, target=target, namespace=namespace, name=name)


def _extension_target(forwarded_props: Any) -> str | None:
    if not isinstance(forwarded_props, dict):
        return None
    extension = forwarded_props.get("agentdeck")
    if not isinstance(extension, dict):
        return None
    target = extension.get("target")
    return target if isinstance(target, str) else None


def _single_resume_entry(resume: list[ResumeEntry]) -> ResumeEntry:
    if len(resume) != 1:
        raise _InvalidRequestError(
            f"exactly one outstanding interrupt is supported; got {len(resume)} resume entries "
            "(several outstanding interrupts is a tracked gap, agui.md)"
        )
    entry = resume[0]
    if entry.status != "resolved":
        raise _InvalidRequestError(f"resume status {entry.status!r} is not supported yet; only 'resolved' is")
    return entry


async def _last_interrupt(run: Run) -> Event:
    events = [event async for event in run.events(follow=False)]
    for event in reversed(events):
        if event.kind == "run.interrupted":
            return event
    raise _InvalidRequestError(f"run {run.id!r} is waiting for an answer but logged no interrupt")


def _reject_unsupported_fields(run_input: RunAgentInput) -> None:
    if run_input.tools:
        raise _InvalidRequestError(
            "frontend tools are not supported yet: non-empty `tools` is refused (agui.md gap table)"
        )
    if run_input.state:
        raise _InvalidRequestError(
            "shared state is not supported yet: non-empty `state` is refused (agui.md gap table)"
        )
    if run_input.context:
        raise _InvalidRequestError(
            "run-scoped context is not supported yet: non-empty `context` is refused (agui.md gap table)"
        )
    if run_input.parent_run_id is not None:
        raise _InvalidRequestError("run branching is not supported yet: `parentRunId` is refused (agui.md gap table)")


async def _parse_body(request: Request) -> RunAgentInput:
    if not await request.body():
        raise _InvalidRequestError("empty request body")
    try:
        raw = await request.json()
    except ValueError as exc:
        raise _InvalidRequestError(f"malformed JSON body: {exc}") from None
    try:
        return RunAgentInput.model_validate(raw)
    except PydanticValidationError as exc:
        raise _InvalidRequestError(f"invalid RunAgentInput: {exc}") from None


def _detail(message: str, code: GatewayFailureCode) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=_STATUS[code])


async def _on_gateway_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GatewayError)
    return _detail(exc.message, exc.code)


async def _on_bad_request(request: Request, exc: Exception) -> JSONResponse:
    return _detail(str(exc), GatewayFailureCode.INVALID_INPUT)


_HANDLERS: dict[Any, Any] = {
    GatewayError: _on_gateway_error,
    _InvalidRequestError: _on_bad_request,
    InputError: _on_bad_request,
}

__all__ = ["AGUI"]
