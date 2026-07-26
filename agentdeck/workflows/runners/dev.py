"""Single-shot graph runner used by :meth:`BaseWorkflow.run`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import Workspace, current_capture
from agentdeck.workflows.runners.base import BaseWorkflowRunner
from agentdeck.workflows.state import coerce_input


@dataclass(slots=True)
class DevWorkflowRunner(BaseWorkflowRunner):
    """Compile-and-invoke driver — every node sees one shared workspace."""

    async def run(self, state: Any = None) -> Any:
        # One root observation makes the whole graph run a single, session-tagged trace
        # (every node's agent/skill spans nested under it) carrying its input/output.
        initial = coerce_input(state, self.workflow.state)
        with trace_run(
            current_capture(),
            name=self.workflow.name or self.workflow.__name__,
            input=initial,
        ) as tr:
            async with Workspace.open(
                environment=self.environment,
                input_files=self.input_files,
            ):
                result = await self.graph.ainvoke(initial, config=self.config)
                tr.set_output(result)
                return result


__all__ = ["DevWorkflowRunner"]
