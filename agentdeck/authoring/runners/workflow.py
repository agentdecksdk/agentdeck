"""Direct-call workflow runner: compiled graph + per-invocation configuration, no event log.

Used by :meth:`~agentdeck.authoring.workflow.Workflow.run`/``run_stream``  -  a Runtime-driven
workflow run never touches this; it goes through ``adapters/executors/langgraph/engine.py``
instead. Sandbox scoping (v1's ``open_sandbox`` around every invocation) is gone with
``BaseSandboxAgent``: no workflow compiled through ``authoring`` needs one in v3.

Like the agent runner beside it, this opens no spans of its own  -  tracing is a Deck-level
capability rendered from the canonical event stream, and a direct call bypasses that stream
entirely, the same way it bypasses the event log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, cast

from langchain_core.runnables import RunnableConfig

from agentdeck.adapters.executors.langgraph.executor import STREAM_CONFIGURABLE_KEY
from agentdeck.authoring.compile import compile_workflow
from agentdeck.authoring.state import coerce_input

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.authoring.workflow import Workflow


@dataclass(slots=True)
class BaseWorkflowRunner:
    """A compiled :class:`CompiledStateGraph` plus invocation defaults."""

    workflow: Workflow
    graph: CompiledStateGraph[Any]
    config: RunnableConfig = field(default_factory=RunnableConfig)

    @classmethod
    def from_workflow(
        cls,
        workflow: Workflow,
        *,
        config: RunnableConfig | None = None,
        **runner_options: Any,
    ) -> Self:
        return cls(
            workflow=workflow,
            graph=compile_workflow(workflow),
            config=config if config is not None else RunnableConfig(),
            **runner_options,
        )

    async def run(self, state: Any = None) -> Any:
        """Drive the configured graph."""
        raise NotImplementedError

    def run_stream(self, state: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Streamed counterpart to :meth:`run`; not abstract, see :class:`DevWorkflowRunner`."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")


@dataclass(slots=True)
class DevWorkflowRunner(BaseWorkflowRunner):
    """Compile-and-invoke driver  -  every node sees one shared workspace."""

    async def run(self, state: Any = None) -> Any:
        initial = coerce_input(state, self.workflow.state)
        return await self.graph.ainvoke(initial, config=self.config)

    async def run_stream(self, state: Any = None) -> AsyncIterator[dict[str, Any]]:
        """One ``astream`` over ``["updates", "custom"]``: a ``node_update`` event per
        completed node, a ``custom`` event per :func:`~langgraph.config.get_stream_writer`
        call (e.g. a nested :class:`~agentdeck.authoring.nodes.AgentNode`'s text deltas),
        then one terminal ``done`` event carrying the final state.
        """
        initial = coerce_input(state, self.workflow.state)
        final_state: Any = initial
        stream_config: RunnableConfig = {
            **self.config,
            "configurable": {**self.config.get("configurable", {}), STREAM_CONFIGURABLE_KEY: True},
        }
        stream = cast(
            "AsyncIterator[tuple[str, Any]]",
            self.graph.astream(initial, config=stream_config, stream_mode=["updates", "custom", "values"]),
        )
        async for mode, chunk in stream:
            if mode == "updates":
                for node, delta in cast("dict[str, Any]", chunk).items():
                    yield {"type": "node_update", "node": node, "delta": delta}
            elif mode == "custom":
                yield {"type": "custom", "data": chunk}
            else:  # "values"  -  tracked for the final state, not surfaced as its own event
                final_state = chunk
        yield {"type": "done", "state": final_state}


__all__ = ["BaseWorkflowRunner", "DevWorkflowRunner"]
