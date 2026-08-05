"""The langgraph engine: ``EnginePort`` over a compiled ``StateGraph``.

``spec.native`` is an *uncompiled* ``StateGraph`` — this adapter compiles it itself, with
its own checkpointer, and caches the result per invocable name (ADR-D5: an engine's
checkpointer is its own working memory, never shared with or read by an outer ring), so
nothing outside this directory ever sees a ``StateGraph``, a checkpointer, or a
``thread_id``'s raw graph state. ``astream(..., stream_mode="updates")`` maps one-to-one
onto the payloads this adapter yields: a ``{node: patch}`` chunk becomes ``node.updated``,
a ``{"__interrupt__": (...)}`` chunk becomes ``run.interrupted`` and ends the stream (the
graph suspends there; resuming re-enters the same ``astream`` call with a
``Command(resume=value)``), and the stream simply ending means the graph reached ``END``.

The ``StateGraph``'s schema must be a ``TypedDict`` (or pydantic model), never a bare
``dict``: langgraph treats a bare ``dict`` as one opaque channel, so a node's return
replaces the *entire* state instead of merging into it, which would silently break the
shallow-merge every ``node.updated`` promises its readers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import StateGraph
from langgraph.types import Command

from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.events import NodeUpdated, RunCompleted, RunInterrupted, Usage
from agentdeck.core.ports import EnginePort
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec

_INTERRUPT_KEY = "__interrupt__"
_KNOWN_REASONS = frozenset({"human", "pause", "approval"})


class LangGraphEngine(EnginePort):
    """Plays ``spec.native`` (an uncompiled ``StateGraph``) through ``astream``.

    One checkpointer for every graph this instance ever plays — a fresh in-memory one by
    default, or a durable one (sqlite/postgres, via ``checkpointer.py``'s
    ``resolve_checkpointer``) passed in explicitly. Mirrors ``OpenAIAgentsEngine``'s
    ``sessions: ExecutionStore | None`` constructor shape, and, unlike
    ``resolve_checkpointer("memory")``, is never shared with another engine instance — two
    engines must not silently see each other's threads.
    """

    engine: ClassVar[str] = "langgraph"

    def __init__(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        self._checkpointer = checkpointer or MemorySaver()
        self._compiled: dict[str, CompiledStateGraph[Any, Any, Any, Any]] = {}

    async def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        graph = self._graph_for(spec)
        # The langgraph thread is scoped to this run: a resume always names the same
        # run_id's thread back via the RunInterrupted it got, so reusing ctx.run_id here
        # needs no separate id-minting step.
        config: RunnableConfig = {"configurable": {"thread_id": ctx.run_id}}
        async for payload in self._play(graph, _to_graph_input(input), config):
            yield payload

    async def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        graph = self._graph_for(spec)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        async for payload in self._play(graph, Command(resume=value), config):
            yield payload

    def _graph_for(self, spec: InvocableSpec) -> CompiledStateGraph[Any, Any, Any, Any]:
        compiled = self._compiled.get(spec.name)
        if compiled is None:
            if not isinstance(spec.native, StateGraph):
                raise ConfigError(
                    f"{spec.name!r} has no langgraph StateGraph: expected native=StateGraph, got {type(spec.native)}"
                )
            compiled = spec.native.compile(checkpointer=self._checkpointer)
            self._compiled[spec.name] = compiled
        return compiled

    async def _play(
        self,
        graph: CompiledStateGraph[Any, Any, Any, Any],
        graph_input: Any,
        config: RunnableConfig,
    ) -> AsyncGenerator[KnownPayload, None]:
        thread_id = config["configurable"]["thread_id"]
        async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
            interrupted = chunk.get(_INTERRUPT_KEY)
            if interrupted is not None:
                yield _run_interrupted(interrupted[0], thread_id)
                return  # the graph suspended; its terminal event arrives on resume
            for node, patch in chunk.items():
                yield NodeUpdated(node=node, state_patch=_jsonable(patch, node))
        state = await graph.aget_state(config)
        yield RunCompleted(output=coerce_input(_dump(state.values)), usage=Usage(input_tokens=0, output_tokens=0))


def _to_graph_input(input: Input) -> dict[str, Any]:
    # M0 scope is UC2's ClaimPipeline fixture, plain text in: images/resources are a
    # follow-up, not a silent drop — better to raise now than feed a node a blank string.
    texts = [block.text for block in input if isinstance(block, TextBlock)]
    if len(texts) != len(input):
        raise ConfigError("langgraph engine (M0) only supports text input blocks")
    return {"input": "\n".join(texts)}


def _run_interrupted(interrupt: Any, thread_id: str) -> RunInterrupted:
    value = interrupt.value
    reason = value.get("reason") if isinstance(value, Mapping) else None
    return RunInterrupted(
        interrupt_id=str(interrupt.id),
        reason=reason if reason in _KNOWN_REASONS else "human",
        payload=_jsonable(value, "interrupt"),
        thread_id=thread_id,
    )


def _jsonable(value: Any, source: str) -> dict[str, Any]:
    # Crude on purpose (M0): a node's update / an interrupt's payload must be a plain
    # mapping to round-trip through the event schema's dict[str, Any] fields — arbitrary
    # objects (a dataclass, a pydantic model) are a follow-up, not a silent misserialize.
    if isinstance(value, Mapping):
        return dict(value)
    raise ConfigError(f"langgraph engine (M0) only supports dict-shaped {source} values, got {type(value)}")


def _dump(values: Any) -> str:
    return str(dict(values)) if isinstance(values, Mapping) else str(values)


__all__ = ["LangGraphEngine"]
