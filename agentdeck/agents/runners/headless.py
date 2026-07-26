"""Single-shot agent runner for graph nodes and tool wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents import Runner, RunResult

from agentdeck.agents.runners.base import BaseRunner
from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import current_capture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(slots=True)
class HeadlessRunner(BaseRunner):
    """Single-invocation runner that inherits or opens a :class:`Workspace`."""

    async def run(self, message: Any = None, *, session: Any = None) -> RunResult:
        # One root observation carries the turn's identity + input/output; OpenInference's
        # spans nest under it. Nested inside a workflow run, this becomes a child of the
        # workflow's root span, re-affirming the same session.
        with trace_run(current_capture(), name=self.agent.name, kind="agent", input=message) as tr:
            async with self.attach_sandbox():
                result = await Runner.run(
                    self.agent,
                    message,
                    run_config=self.run_config,
                    max_turns=self.max_turns,
                    session=session,
                )
                tr.set_output(result.final_output)
                return result

    async def run_streamed(self, message: Any = None, *, session: Any = None) -> AsyncIterator[str]:
        """Async-generator counterpart to :meth:`run`: yields text deltas as the turn streams.

        Same trace span, sandbox lifecycle, ``run_config``/``max_turns``/``session`` as
        ``run`` — they just span the whole generator instead of one await, since the
        trace's output (and the sandbox's teardown) can only happen once the caller has
        drained every delta.
        """
        with trace_run(current_capture(), name=self.agent.name, kind="agent", input=message) as tr:
            async with self.attach_sandbox():
                result = Runner.run_streamed(
                    self.agent,
                    message,
                    run_config=self.run_config,
                    max_turns=self.max_turns,
                    session=session,
                )
                async for event in result.stream_events():
                    # Only the raw model text deltas matter for a chat UI; tool-call /
                    # handoff / agent-updated events are structural noise here.
                    if event.type == "raw_response_event" and event.data.type == "response.output_text.delta":
                        yield event.data.delta
                tr.set_output(result.final_output)


__all__ = ["HeadlessRunner"]
