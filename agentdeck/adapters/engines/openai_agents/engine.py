"""The openai-agents engine: ``EnginePort`` over ``agents.Runner``.

``spec.native`` is the pre-built ``agents.Agent`` (handoffs and tools included) — this
adapter only runs it and translates its stream, per ``core/ports/engine.py``. Execution
state (the SDK session) is engine-private (ADR-D5): the session, not the log, is what
feeds the model. The log passed in as ``history`` is read for exactly one purpose — the
turn-start reconciliation in ``reconcile.py``, which repairs a session left behind by a
crash between the log write and the session write.
"""

from __future__ import annotations

import dataclasses
import json
import os
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

from agents import Agent, RunConfig, Runner
from pydantic import BaseModel

from agentdeck.adapters.engines.openai_agents.reconcile import reconcile
from agentdeck.adapters.engines.openai_agents.sessions import ExecutionStore
from agentdeck.adapters.engines.openai_agents.translate import translate
from agentdeck.core.content import DataBlock, TextBlock, coerce_input
from agentdeck.core.events import RunCancelled, RunCompleted, Usage
from agentdeck.core.ports import EnginePort
from agentdeck.core.ports.control import RunCancelledError
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from agents.memory.session import Session
    from agents.result import RunResultStreaming
    from agents.usage import Usage as SDKUsage

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec


@dataclass(slots=True)
class Launch:
    """One run's SDK handle, plus whether this engine reached its terminal payload.

    ``finished`` exists because nothing on the SDK result can answer that question at the
    moment it is asked: a run abandoned mid-stream and a run that ended normally both arrive
    at ``_launch``'s exit already cancelled, so ``is_complete`` is true either way, and
    ``final_output`` is only *usually* absent from the abandoned one (the SDK's run loop is
    detached, so it may well have finished while nobody was reading). The engine's own control
    flow is the authority, and this is how it says so.

    It is the *engine's* view, not the log's, and cannot be made the log's: it is set before the
    terminal payload is yielded, because the Runtime breaks on that payload and never returns
    here. So a store that rejects the terminal append leaves this ``True`` while the log ends in
    ``run.failed`` — an observability span reporting success for a run the log calls failed. The
    log is the record; a reader reconciling the two believes the log.
    """

    result: RunResultStreaming
    finished: bool = False


class OpenAIAgentsEngine(EnginePort):
    """Plays ``spec.native`` (an ``agents.Agent``) through ``Runner.run_streamed``.

    Four protected seams exist for the v1 bridge in ``agentdeck/v1bridge/engine.py``, which needs a
    differently-configured run and a v1-shaped result but the same stream handling:
    :meth:`_session` picks the execution state, :meth:`_launch` starts the SDK run,
    :meth:`_translate` maps one stream event, and :meth:`_terminal` closes the run.
    """

    engine: ClassVar[str] = "openai-agents"

    def __init__(self, sessions: ExecutionStore | None = None) -> None:
        self._sessions = sessions or ExecutionStore()

    async def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        agent = _agent_of(spec)
        session = self._session(ctx)
        if session is not None:
            diverged = await reconcile(session, history)
            if diverged is not None:
                # Two stores disagreeing is worth a place in the record, not just a log line;
                # the run itself still has the session it needs and plays on.
                yield diverged
        async with self._launch(agent, _to_sdk_input(input), ctx, session) as launch:
            result = launch.result
            tool_names: dict[str, str] = {}
            # The SDK's run loop is a detached task; an abandoned generator must cancel it
            # explicitly (mirrors agents/runners/headless.py's run_streamed, same reason).
            stream = cast("AsyncGenerator[Any, None]", result.stream_events())
            try:
                async with aclosing(stream) as events:
                    async for event in events:
                        payload = self._translate(event, tool_names)
                        if payload is not None:
                            yield payload
                        try:
                            await ctx.gate.checkpoint()
                        except RunCancelledError:
                            # A complete chunk was just yielded (or none was, at the very
                            # first safe point) — never a partial one — so this is the next
                            # safe point the contract promises, not "right now, mid-token".
                            result.cancel()
                            yield RunCancelled(reason="cancel signal")
                            return
            except BaseException:
                result.cancel()
                raise
            result.cancel()
            terminal = self._terminal(result)
            # Set before the yields, not after them: the Runtime breaks on the terminal event,
            # so the line after this loop never runs.
            launch.finished = True
            for payload in terminal:
                yield payload

    def _session(self, ctx: RunContext) -> Session | None:
        """The execution state this run reads and writes — the adapter's own store by default."""
        return self._sessions.session_for(ctx)

    @asynccontextmanager
    async def _launch(
        self, agent: Agent[Any], message: str, ctx: RunContext, session: Session | None
    ) -> AsyncIterator[Launch]:
        """Start the run and hold whatever scope it needs open until the stream is drained.

        Lifecycle an override must respect: **code after the ``yield`` may never run.** A
        successful run ends with the Runtime breaking on the terminal event, which closes this
        generator — the ``yield`` raises ``GeneratorExit`` and the lines below it are skipped.
        Anything that must happen once per finished run therefore belongs in the
        ``GeneratorExit`` path, keyed on ``Launch.finished``, never only after the ``yield``.
        """
        yield Launch(
            Runner.run_streamed(
                agent,
                message,
                # The run context travels as the SDK's own context object, which is the one thing
                # the SDK hands a function tool: a tool declaring ``RunContextWrapper[RunContext]``
                # reaches ``wrapper.context.reporter`` (and the gate) without importing a Runtime.
                # Nothing in the SDK reads it — it is opaque to the run loop by design.
                context=ctx,
                session=session,
                run_config=RunConfig(tracing_disabled=not _tracing_enabled()),
            )
        )

    def _translate(self, event: Any, tool_names: dict[str, str]) -> KnownPayload | None:
        return translate(event, tool_names)

    def _terminal(self, result: RunResultStreaming) -> Sequence[KnownPayload]:
        return (_run_completed(result),)

    async def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        # M0 scope is UC1's plain chat, which never suspends — there is no interrupted run
        # for this engine to continue. Raising (not a silent no-op) matches the Runtime's
        # own rule that this method is only ever called on a WAITING_HUMAN run.
        raise ConfigError(f"openai-agents engine (M0) has no interrupts to resume: {spec.name!r} never suspends")
        yield  # pragma: no cover — makes this an async generator; never reached


def _tracing_enabled() -> bool:
    """Opt-in switch for the SDK's default trace exporter (issue #61).

    Off by default: a keyless/fake-model run (tests, CI, the M0 demo) has no OpenAI
    account to export traces to, and the SDK's exporter otherwise attempts a real HTTPS
    call on every run, logging a non-fatal ``Tracing client error 401``. Set
    ``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED=true`` to restore it for a deployment that
    wants the SDK's own trace export (as opposed to v1's Langfuse/OpenInference route).
    """
    raw = os.environ.get("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED")
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def _agent_of(spec: InvocableSpec) -> Agent[Any]:
    if not isinstance(spec.native, Agent):
        raise ConfigError(f"{spec.name!r} has no openai-agents Agent: expected native=Agent, got {type(spec.native)}")
    return spec.native


def _to_sdk_input(input: Input) -> str:
    # M0 scope is plain-text chat; images/resources are a follow-up, not a silent
    # drop — better to raise now than answer a question the model never saw.
    texts = [block.text for block in input if isinstance(block, TextBlock)]
    if len(texts) != len(input):
        raise ConfigError("openai-agents engine (M0) only supports text input blocks")
    return "\n".join(texts)


def _run_completed(result: RunResultStreaming) -> RunCompleted:
    output = result.final_output
    if isinstance(output, str):
        return RunCompleted(output=coerce_input(output), usage=_usage_of(result))
    return RunCompleted(output=[DataBlock(data=_structured(output))], usage=_usage_of(result))


def _structured(output: Any) -> Any:
    """An ``output_type`` agent's validated result as JSON data.

    It travels as a ``DataBlock``, which is why this no longer raises: refusing a non-``str``
    final output turned a documented feature into a failed run. The ceiling, and it applies to
    every branch below: a leaf JSON cannot carry becomes its ``str()`` — a non-finite float
    included, since ``null`` would claim it was absent — rather than failing the run at its
    last event.
    """
    if isinstance(output, BaseModel):
        try:
            output = output.model_dump(mode="json")
        except ValueError:
            # PydanticSerializationError, which is a ValueError: one leaf pydantic cannot
            # render as JSON. The python dump keeps the rest and the net below takes that
            # leaf, so only its fidelity is lost — not the whole run's terminal event.
            output = output.model_dump()
    elif dataclasses.is_dataclass(output) and not isinstance(output, type):
        output = dataclasses.asdict(output)
    return json.loads(json.dumps(output, default=str), parse_constant=str)


def _usage_of(result: RunResultStreaming) -> Usage:
    usage: SDKUsage | None = getattr(result.context_wrapper, "usage", None)
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


__all__ = ["Launch", "OpenAIAgentsEngine"]
