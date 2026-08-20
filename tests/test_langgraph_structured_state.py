"""The langgraph adapter's structured channel, both directions: a graph's initial state
arrives as one ``DataBlock`` and its final state leaves as one on ``run.completed``.

This is what a workflow endpoint needs  -  v1 posts an arbitrary JSON state and gets the final
state back  -  and neither direction fits text: one channel in, a stringified dict out. The
node here reads a field only a state-shaped input can carry, so a mapping that quietly
dropped it would fail these tests rather than pass them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from agentdeck.adapters.executors.langgraph import LangGraphExecutor
from agentdeck.core.content import DataBlock, TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunCompleted, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from agentdeck.core.content import Input

NO_USAGE = Usage(input_tokens=0, output_tokens=0)


class ShipState(TypedDict, total=False):
    input: str
    priority: str
    expedited: bool


def _ship(state: ShipState) -> ShipState:
    return {"expedited": state.get("priority") == "high"}


def _spec() -> InvocableSpec:
    graph: StateGraph[Any] = StateGraph(ShipState)
    graph.add_node("ship", _ship)
    graph.add_edge(START, "ship")
    graph.add_edge("ship", END)
    return InvocableSpec(name="Shipper", kind=InvocableKind.WORKFLOW, executor=LangGraphExecutor.name, native=graph)


def _ctx() -> RunContext:
    return RunContext(namespace="acme", run_id="r-1", session_id="s-1")


async def _completed(input: Input, spec: InvocableSpec | None = None) -> RunCompleted:
    payloads = [payload async for payload in LangGraphExecutor().start(spec or _spec(), input, [], _ctx())]
    terminal = payloads[-1]
    assert isinstance(terminal, RunCompleted)
    return terminal


async def test_the_final_state_leaves_as_one_data_block() -> None:
    completed = await _completed(coerce_input("order 41"))
    assert completed == RunCompleted(
        output=[DataBlock(data={"input": "order 41", "expedited": False})],
        usage=NO_USAGE,
    )


async def test_a_state_shaped_input_reaches_the_graph_whole() -> None:
    """``priority`` has no text channel to arrive on: only the state-shaped input carries it,
    and only the node reading it can set ``expedited``."""
    completed = await _completed([DataBlock(data={"input": "order 41", "priority": "high"})])
    assert completed.output == [DataBlock(data={"input": "order 41", "priority": "high", "expedited": True})]


async def test_text_input_still_fills_the_one_channel() -> None:
    completed = await _completed(coerce_input("order 41"))
    assert completed.output == [DataBlock(data={"input": "order 41", "expedited": False})]


async def test_a_data_block_mixed_with_text_is_refused() -> None:
    with pytest.raises(ConfigError, match="one data block"):
        await _completed([TextBlock(text="order 41"), DataBlock(data={"priority": "high"})])


async def test_a_data_block_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ConfigError, match="JSON object"):
        await _completed([DataBlock(data=["order 41"])])


async def test_a_non_finite_float_in_the_state_becomes_its_token_not_null() -> None:
    """A node returning ``float("nan")`` is ordinary Python and would have serialized as
    ``null``: the consumer would read "no ratio" off an event whose store row says the same,
    with nothing anywhere recording that a number was lost."""

    class RatioState(TypedDict, total=False):
        input: str
        ratio: float

    def _rate(_state: RatioState) -> RatioState:
        return {"ratio": float("nan")}

    graph: StateGraph[Any] = StateGraph(RatioState)
    graph.add_node("rate", _rate)
    graph.add_edge(START, "rate")
    graph.add_edge("rate", END)
    spec = InvocableSpec(name="Rater", kind=InvocableKind.WORKFLOW, executor=LangGraphExecutor.name, native=graph)

    completed = await _completed(coerce_input("order 41"), spec)
    assert completed.output == [DataBlock(data={"input": "order 41", "ratio": "NaN"})]


async def test_a_state_leaf_that_is_not_json_becomes_its_string() -> None:
    """The declared ceiling: the old ``str(dict(values))`` stringified the whole state, so a
    graph that completed before still completes  -  it does not fail at its last event."""

    class StampState(TypedDict, total=False):
        input: str
        at: Any

    def _stamp(_state: StampState) -> StampState:
        return {"at": datetime(2026, 1, 1, tzinfo=UTC)}

    graph: StateGraph[Any] = StateGraph(StampState)
    graph.add_node("stamp", _stamp)
    graph.add_edge(START, "stamp")
    graph.add_edge("stamp", END)
    spec = InvocableSpec(name="Stamper", kind=InvocableKind.WORKFLOW, executor=LangGraphExecutor.name, native=graph)

    completed = await _completed(coerce_input("order 41"), spec)
    assert completed.output == [DataBlock(data={"input": "order 41", "at": "2026-01-01 00:00:00+00:00"})]
