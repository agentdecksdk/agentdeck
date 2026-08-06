"""The engine v1's own surface runs on: an ``EnginePort`` whose runs are configured exactly
the way ``agents/runners/headless.py`` configures them — v1's runner *is* what configures
them, called rather than reimplemented.

The M0 engine builds a minimal ``RunConfig`` of its own, which is right for a v2 caller
and wrong for a v1 one: v1's chat resolves the model provider, temperature, ``max_turns``
and CA bundle from settings, opens a sandbox when the agent needs one, and wraps the turn
in one Langfuse observation. That resolution is v1's, so it is *reused* here rather than
reimplemented — this class calls v1's runner for it and keeps the M0 engine's stream
translation. It is the same ``engine`` name, so a composition root registers one or the
other, never both.

Two things v1 puts on the wire have no canonical home yet, so both are additive and
namespaced (D10: an engine translates into existing kinds or namespaces a ``custom``
event, it never mints one): the per-model-call token counts v1 aggregates into
``usage.requests`` become ``usage.reported``, and a structured ``output_type`` result —
which ``RunCompleted.output`` can only carry as text — rides alongside as
``openai_agents.structured_output``.
"""

from __future__ import annotations

import dataclasses
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agents import Runner
from pydantic import BaseModel

from agentdeck.adapters.engines.openai_agents.engine import Launch, OpenAIAgentsEngine
from agentdeck.agents.runners import HeadlessRunner
from agentdeck.core.content import coerce_input
from agentdeck.core.events import Custom, RunCompleted, Usage, UsageReported
from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import current_capture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from agents import Agent
    from agents.memory.session import Session
    from agents.result import RunResultStreaming

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload

STRUCTURED_OUTPUT = "openai_agents.structured_output"


class V1CompatEngine(OpenAIAgentsEngine):
    """Runs an agent with v1's resolved run config, sandbox scope and trace span.

    ``session_for`` is v1's own session lookup (``App.session_for``), taken rather than
    rebuilt so a conversation is one conversation whether the turn arrived through
    ``App.chat`` or through the HTTP surface — the adapter's own ``ExecutionStore`` keys
    sessions by tenant and would split the two apart.
    """

    def __init__(self, session_for: Callable[[str], Session] | None = None) -> None:
        super().__init__()
        self._session_for = session_for

    def _session(self, ctx: RunContext) -> Session | None:
        """v1's session for a conversation, none for a one-shot run — which is what v1's
        ``run_agent`` passes. Without an injected lookup this falls back to the adapter's own
        execution store, so a code-first caller still gets memory rather than silently losing it.
        """
        if ctx.session_id is None:
            return None
        if self._session_for is not None:
            return self._session_for(ctx.session_id)
        return self._sessions.session_for(ctx)

    @asynccontextmanager
    async def _launch(
        self, agent: Agent[Any], message: str, ctx: RunContext, session: Session | None
    ) -> AsyncIterator[Launch]:
        runner = HeadlessRunner.from_agent(agent)
        with trace_run(
            current_capture(), name=agent.name, kind="agent", input=message, session_id=ctx.session_id
        ) as tr:
            async with runner.attach_sandbox():
                launch = Launch(
                    Runner.run_streamed(
                        agent, message, run_config=runner.run_config, max_turns=runner.max_turns, session=session
                    )
                )
                try:
                    yield launch
                except GeneratorExit:
                    # How a *successful* run ends, not a failure — the Runtime stops reading at
                    # the terminal event, which closes the generator suspended here. Reporting
                    # this as an error is what made every completed turn look failed.
                    if launch.finished:
                        tr.set_output(launch.result.final_output)
                    else:
                        # Not necessarily a consumer walking away: a control-gate cancel ends the
                        # run here too, and both are "closed before the terminal event".
                        tr.set_output(error="GeneratorExit: run did not reach its terminal event")
                    raise
                except BaseException as exc:
                    tr.set_output(error=f"{type(exc).__name__}: {exc}")
                    raise
                tr.set_output(launch.result.final_output)

    def _translate(self, event: Any, tool_names: dict[str, str]) -> KnownPayload | None:
        payload = super()._translate(event, tool_names)
        if payload is not None:
            return payload
        return _usage_reported(event)

    def _terminal(self, result: RunResultStreaming) -> Sequence[KnownPayload]:
        output = result.final_output
        usage = _usage_of(result)
        if isinstance(output, str):
            return (RunCompleted(output=coerce_input(output), usage=usage),)
        structured = _jsonable(output)
        return (
            Custom(name=STRUCTURED_OUTPUT, data={"output": structured}),
            RunCompleted(output=coerce_input(json.dumps(structured, default=str)), usage=usage),
        )


def _usage_reported(event: Any) -> KnownPayload | None:
    """One finished model call → one ``usage.reported``, which is what v1's
    ``usage.requests`` counts."""
    if event.type != "raw_response_event" or getattr(event.data, "type", None) != "response.completed":
        return None
    response = event.data.response
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return UsageReported(
        model=str(getattr(response, "model", "") or ""),
        usage=Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens),
    )


def _usage_of(result: RunResultStreaming) -> Usage:
    usage = getattr(result.context_wrapper, "usage", None)
    if usage is None:
        return Usage(input_tokens=0, output_tokens=0)
    return Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def _jsonable(value: Any) -> Any:
    """A structured final output as plain JSON data — the shape v1's endpoints put on the wire."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    return str(value)


__all__ = ["STRUCTURED_OUTPUT", "V1CompatEngine"]
