"""Base workflow runner: compiled graph + per-invocation configuration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self

from langchain_core.runnables import RunnableConfig

from agentdeck.runtime.observability import init_observability
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    import os
    from collections.abc import AsyncIterator, Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.workflows.base import BaseWorkflow


@dataclass(slots=True)
class BaseWorkflowRunner(ABC):
    """A compiled :class:`CompiledStateGraph` plus invocation defaults."""

    workflow: type[BaseWorkflow]
    graph: CompiledStateGraph[Any]
    config: RunnableConfig = field(default_factory=RunnableConfig)
    input_files: Sequence[str | os.PathLike[str]] = ()
    environment: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_workflow(
        cls,
        workflow: type[BaseWorkflow],
        *,
        config: RunnableConfig | None = None,
        input_files: Sequence[str | os.PathLike[str]] | None = None,
        environment: Mapping[str, str] | None = None,
        **runner_options: Any,
    ) -> Self:
        # Start Langfuse once (no-op when disabled). Workflow tracing rides the shared OTel
        # context, not a LangChain callback: the run wraps ``graph.ainvoke`` in
        # ``observability.trace_run`` (a root span), and every node's agent (OpenInference)
        # and skill (TRACEPARENT) spans nest under it — no ``langchain`` meta-package needed.
        init_observability()
        return cls(
            workflow=workflow,
            graph=workflow.build(),
            config=config if config is not None else RunnableConfig(),
            input_files=tuple(input_files or ()),
            environment=get_settings().sandbox_env(environment),
            **runner_options,
        )

    @abstractmethod
    async def run(self, state: Any = None) -> Any:
        """Drive the configured graph."""

    def run_stream(self, state: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Streamed counterpart to :meth:`run`; not abstract, see :class:`DevWorkflowRunner`."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")


__all__ = ["BaseWorkflowRunner"]
