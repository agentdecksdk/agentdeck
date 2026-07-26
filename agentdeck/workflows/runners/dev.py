"""Single-shot graph runner used by :meth:`BaseWorkflow.run`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from agentdeck.runtime.observability import trace_run
from agentdeck.runtime.workspace import Workspace, current_capture
from agentdeck.workflows.runners.base import BaseWorkflowRunner
from agentdeck.workflows.state import coerce_input

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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

    async def run_stream(self, state: Any = None) -> AsyncIterator[dict[str, Any]]:
        """One ``astream`` over ``["updates", "custom"]``: a ``node_update`` event per
        completed node, a ``custom`` event per :func:`~langgraph.config.get_stream_writer`
        call (e.g. a nested :class:`~agentdeck.workflows.nodes.AgentNode`'s text deltas),
        then one terminal ``done`` event carrying the final state.
        """
        initial = coerce_input(state, self.workflow.state)
        final_state: Any = initial
        with trace_run(
            current_capture(),
            name=self.workflow.name or self.workflow.__name__,
            input=initial,
        ) as tr:
            async with Workspace.open(
                environment=self.environment,
                input_files=self.input_files,
            ):
                # Multi-mode astream yields (mode, chunk) tuples at runtime; the SDK's stub
                # only declares the single-mode `dict[str, Any] | Any` shape, hence the casts.
                stream = cast(
                    "AsyncIterator[tuple[str, Any]]",
                    self.graph.astream(initial, config=self.config, stream_mode=["updates", "custom", "values"]),
                )
                async for mode, chunk in stream:
                    if mode == "updates":
                        for node, delta in cast("dict[str, Any]", chunk).items():
                            yield {"type": "node_update", "node": node, "delta": delta}
                    elif mode == "custom":
                        yield {"type": "custom", "data": chunk}
                    else:  # "values" — tracked for the final state, not surfaced as its own event
                        final_state = chunk
                tr.set_output(final_state)
                yield {"type": "done", "state": final_state}


__all__ = ["DevWorkflowRunner"]
