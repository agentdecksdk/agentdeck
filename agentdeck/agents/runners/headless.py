"""Single-shot agent runner for graph nodes and tool wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents import Runner, RunResult

from agentdeck.agents.runners.base import BaseRunner
from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import current_capture


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


__all__ = ["HeadlessRunner"]
