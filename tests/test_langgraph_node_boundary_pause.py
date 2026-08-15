"""Resuming a langgraph run paused at a node boundary (#128): a resume continues from where
the graph would have been had it never paused, never replaying a node that already ran.

Distinct from ``tests/contract/test_control.py``'s langgraph case, which holds this engine to
the same cross-engine pause/resume/cancel contract every engine gets. This file is the engine's
own guarantee: that "continues from the node boundary" is real, not just "produces output" —
and the three-way split the ruling on #128 drew between ``durable=True``, ``durable=False`` in
the process that paused it, and ``durable=False`` from another one.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError
from agentdeck.runtime.discovery import DURABLE_KEY
from agentdeck.runtime.service import Runtime


class _State(TypedDict, total=False):
    input: str
    calls: list[str]


def _node(name: str) -> Any:
    def fn(state: _State) -> _State:
        return {"calls": [*state.get("calls", []), name]}

    return fn


def _graph() -> StateGraph[Any]:
    """Three nodes in a line: enough for a pause after the first to leave real work — "b" and
    "c" — for the resume, and for a re-run of "a" to show up in ``calls`` if one happened."""
    g: StateGraph[Any] = StateGraph(_State)
    for name in ("a", "b", "c"):
        g.add_node(name, _node(name))
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", END)
    return g


def _spec(*, durable: bool | None = None) -> InvocableSpec:
    metadata = {} if durable is None else {DURABLE_KEY: durable}
    return InvocableSpec(
        name="Grapher", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=_graph(), metadata=metadata
    )


def _one_node_graph() -> StateGraph[Any]:
    g: StateGraph[Any] = StateGraph(_State)
    g.add_node("only", _node("only"))
    g.add_edge(START, "only")
    g.add_edge("only", END)
    return g


def _one_node_spec() -> InvocableSpec:
    return InvocableSpec(
        name="OneNode", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=_one_node_graph()
    )


def _kinds(events: list[Any]) -> list[str]:
    return [event.kind for event in events]


def _node_updates(events: list[Any]) -> list[str]:
    return [event.payload.node for event in events if event.kind == "node.updated"]


async def test_resume_continues_from_the_node_boundary_without_rerunning_a_completed_node() -> None:
    """The guarantee itself. "a" ran once before the pause; the resume must run "b" and "c" —
    the work the pause left behind — and must not run "a" again."""
    spec = _spec()
    engine = LangGraphEngine()
    store = MemoryEventStore()
    control = MemoryControlPort()
    runtime = Runtime([engine], store, {spec.name: spec}, control=control, control_poll_interval=0.0)
    ctx = RunContext(namespace="acme", run_id="r-node-boundary", session_id="s-node-boundary")

    await control.signal(ctx.run_id, Signal.PAUSE)
    paused = [
        event
        async for event in runtime.run(
            spec.name, coerce_input("go"), run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    assert _kinds(paused)[-3:] == ["control.requested", "control.observed", "run.paused"]
    assert _node_updates(paused) == ["a"]

    resumed = [event async for event in runtime.resume_run(ctx.run_id, namespace=ctx.namespace)]

    assert _node_updates(resumed) == ["b", "c"]  # "a" never runs a second time
    completed = next(event for event in resumed if event.kind == "run.completed")
    assert completed.payload.output[0].data == {"input": "go", "calls": ["a", "b", "c"]}


async def test_a_pause_at_the_last_node_boundary_resumes_straight_to_completion() -> None:
    """The pause can land after the graph's *only* node, with nothing left to run. The resume
    must still complete cleanly with the accumulated state and no ``node.updated`` at all --
    not even langgraph's own replay of the step its checkpoint was loaded from, which a
    one-node graph makes the *only* step there is to replay."""
    spec = _one_node_spec()
    engine = LangGraphEngine()
    store = MemoryEventStore()
    control = MemoryControlPort()
    runtime = Runtime([engine], store, {spec.name: spec}, control=control, control_poll_interval=0.0)
    ctx = RunContext(namespace="acme", run_id="r-last-boundary", session_id="s-last-boundary")

    await control.signal(ctx.run_id, Signal.PAUSE)
    paused = [
        event
        async for event in runtime.run(
            spec.name, coerce_input("go"), run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    assert _kinds(paused)[-1] == "run.paused"
    assert _node_updates(paused) == ["only"]

    resumed = [event async for event in runtime.resume_run(ctx.run_id, namespace=ctx.namespace)]

    assert _node_updates(resumed) == []  # nothing left to run, and the replay is filtered too
    completed = next(event for event in resumed if event.kind == "run.completed")
    assert completed.payload.output[0].data == {"input": "go", "calls": ["only"]}


async def test_a_durable_pause_can_be_resumed_from_another_process() -> None:
    """``durable=True``: the checkpoint lives in a backend every process can reach, so a
    second engine instance — standing in for a worker in another process, per ADR-D5 — can
    lift the pause exactly like the one that recorded it."""
    shared_checkpoint = MemorySaver()  # stands in for a real backend two processes would open
    spec = _spec(durable=True)
    store = MemoryEventStore()
    control = MemoryControlPort()
    ctx = RunContext(namespace="acme", run_id="r-durable", session_id="s-durable")

    runtime1 = Runtime(
        [LangGraphEngine(checkpointer=shared_checkpoint)],
        store,
        {spec.name: spec},
        control=control,
        control_poll_interval=0.0,
    )
    await control.signal(ctx.run_id, Signal.PAUSE)
    paused = [
        event
        async for event in runtime1.run(
            spec.name, coerce_input("go"), run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    assert _kinds(paused)[-1] == "run.paused"

    runtime2 = Runtime(
        [LangGraphEngine(checkpointer=shared_checkpoint)],
        store,
        {spec.name: spec},
        control=control,
        control_poll_interval=0.0,
    )
    resumed = [event async for event in runtime2.resume_run(ctx.run_id, namespace=ctx.namespace)]

    assert _node_updates(resumed) == ["b", "c"]
    assert _kinds(resumed)[-1] == "run.completed"


async def test_resuming_a_non_durable_pause_from_another_process_is_refused() -> None:
    """``durable`` absent (or ``True`` with no backend configured) checkpoints in the engine's
    own memory (ADR-D5) — a second engine instance has none of it, so lifting the pause there
    must be refused, never quietly replayed from the entry node with empty state."""
    spec = _spec()
    store = MemoryEventStore()
    control = MemoryControlPort()
    ctx = RunContext(namespace="acme", run_id="r-cross-process", session_id="s-cross-process")

    runtime1 = Runtime([LangGraphEngine()], store, {spec.name: spec}, control=control, control_poll_interval=0.0)
    await control.signal(ctx.run_id, Signal.PAUSE)
    paused = [
        event
        async for event in runtime1.run(
            spec.name, coerce_input("go"), run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    assert _kinds(paused)[-1] == "run.paused"

    # "another process": a second engine instance with its own in-memory checkpointer, sharing
    # only the log store and the control port.
    runtime2 = Runtime([LangGraphEngine()], store, {spec.name: spec}, control=control, control_poll_interval=0.0)

    resumed: list[Any] = []
    with pytest.raises(ConfigError, match="durable"):
        async for event in runtime2.resume_run(ctx.run_id, namespace=ctx.namespace):
            resumed.append(event)

    assert _kinds(resumed) == ["run.resumed", "run.failed"]


async def test_a_durable_false_pause_is_refused_even_in_the_same_process() -> None:
    """``durable=False`` compiles with no checkpointer at all (mirrors the ``interrupt()``
    refusal), so there is nothing to continue from even when the very same engine instance
    that recorded the pause is the one asked to lift it."""
    spec = _spec(durable=False)
    engine = LangGraphEngine()
    store = MemoryEventStore()
    control = MemoryControlPort()
    runtime = Runtime([engine], store, {spec.name: spec}, control=control, control_poll_interval=0.0)
    ctx = RunContext(namespace="acme", run_id="r-non-durable", session_id="s-non-durable")

    await control.signal(ctx.run_id, Signal.PAUSE)
    paused = [
        event
        async for event in runtime.run(
            spec.name, coerce_input("go"), run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    assert _kinds(paused)[-1] == "run.paused"

    resumed: list[Any] = []
    with pytest.raises(ConfigError, match="durable=False"):
        async for event in runtime.resume_run(ctx.run_id, namespace=ctx.namespace):
            resumed.append(event)

    assert _kinds(resumed) == ["run.resumed", "run.failed"]
