"""The openai-agents engine: ``EnginePort`` over ``agents.Runner``.

``spec.native`` is the pre-built ``agents.Agent`` (handoffs and tools included) — this
adapter only runs it and translates its stream, per ``core/ports/engine.py``. Execution
state (the SDK session) is engine-private (ADR-D5): the event log passed in as
``history`` is not read here, because the whole point of the ADR is that the session,
not the log, feeds the model.
"""

from __future__ import annotations

import os
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, ClassVar, cast

from agents import Agent, RunConfig, Runner

from agentdeck.adapters.engines.openai_agents.sessions import ExecutionStore
from agentdeck.adapters.engines.openai_agents.translate import translate
from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.events import RunCancelled, RunCompleted, Usage
from agentdeck.core.ports import EnginePort
from agentdeck.core.ports.control import RunCancelledError
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from agents.result import RunResultStreaming
    from agents.usage import Usage as SDKUsage

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec


class OpenAIAgentsEngine(EnginePort):
    """Plays ``spec.native`` (an ``agents.Agent``) through ``Runner.run_streamed``."""

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
        session = self._sessions.session_for(ctx)
        run_config = RunConfig(tracing_disabled=not _tracing_enabled())
        result = Runner.run_streamed(agent, _to_sdk_input(input), session=session, run_config=run_config)
        tool_names: dict[str, str] = {}
        # The SDK's run loop is a detached task; an abandoned generator must cancel it
        # explicitly (mirrors agents/runners/headless.py's run_streamed, same reason).
        stream = cast("AsyncGenerator[Any, None]", result.stream_events())
        try:
            async with aclosing(stream) as events:
                async for event in events:
                    payload = translate(event, tool_names)
                    if payload is not None:
                        yield payload
                    try:
                        await ctx.gate.checkpoint()
                    except RunCancelledError:
                        # A complete chunk was just yielded (or none was, at the very first
                        # safe point) — never a partial one — so this is the next safe
                        # point the contract promises, not "right now, mid-token".
                        result.cancel()
                        yield RunCancelled(reason="cancel signal")
                        return
        except BaseException:
            result.cancel()
            raise
        else:
            result.cancel()
        yield _run_completed(result)

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
    if not isinstance(output, str):
        raise ConfigError(f"openai-agents engine (M0) only supports str final_output, got {type(output)}")
    return RunCompleted(output=coerce_input(output), usage=_usage_of(result))


def _usage_of(result: RunResultStreaming) -> Usage:
    usage: SDKUsage | None = getattr(result.context_wrapper, "usage", None)
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


__all__ = ["OpenAIAgentsEngine"]
