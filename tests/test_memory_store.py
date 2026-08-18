"""The memory event log: ordering, ranged reads, and namespace isolation."""

from __future__ import annotations

import pytest

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import KnownPayload, RunCompleted, RunStarted, TextDelta, Usage
from agentdeck.errors import StoreError

ORIGIN = "Greeter"


def _deltas(count: int) -> list[KnownPayload]:
    return [TextDelta(message_id="m1", text=f"chunk {which}") for which in range(count)]


def _ctx(namespace: str = "acme", run_id: str = "r-1") -> RunContext:
    return RunContext(namespace=namespace, run_id=run_id, session_id="s-1")


async def test_events_read_back_in_the_order_they_were_appended() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", _deltas(2), ctx, ORIGIN)
    await store.append("s-1", _deltas(1), ctx, ORIGIN)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0, 1, 2]


async def test_from_seq_is_inclusive_so_zero_reads_the_whole_run() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", _deltas(3), ctx, ORIGIN)
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=0)] == [0, 1, 2]
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=2)] == [2]


async def test_a_seq_range_covers_one_run_and_never_splices_two() -> None:
    """``seq`` restarts at 0 per run, so a range over the whole log would return the tail of
    every run in it  -  which is why a range read has to name the run."""
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", _deltas(2), ctx, ORIGIN)
    await store.append("s-1", _deltas(2), _ctx(run_id="r-2"), ORIGIN)

    tail = await store.read_run("s-1", "r-2", ctx, from_seq=1)
    assert [(event.run_id, event.seq) for event in tail] == [("r-2", 1)]
    assert [(event.run_id, event.seq) for event in await store.read("s-1", ctx)] == [
        ("r-1", 0),
        ("r-1", 1),
        ("r-2", 0),
        ("r-2", 1),
    ]


async def test_an_unknown_log_reads_as_empty() -> None:
    assert await MemoryEventStore().read("nobody", _ctx()) == []
    assert await MemoryEventStore().read_run("nobody", "r-1", _ctx()) == []


async def test_one_namespace_cannot_read_another_namespaces_log_under_the_same_key() -> None:
    """Two namespaces are free to pick the same session id; the store keeps them apart."""
    store = MemoryEventStore()
    await store.append("s-1", _deltas(1), _ctx("acme"), ORIGIN)
    await store.append("s-1", _deltas(1), _ctx("globex"), ORIGIN)

    acme = await store.read("s-1", _ctx("acme"))
    globex = await store.read("s-1", _ctx("globex"))
    assert [event.namespace for event in acme] == ["acme"]
    assert [event.namespace for event in globex] == ["globex"]


async def test_a_read_cannot_be_used_to_mutate_the_log() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    (event,) = await store.append("s-1", _deltas(1), ctx, ORIGIN)
    (await store.read("s-1", ctx)).append(event)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0]


async def test_the_stub_completion_payload_round_trips_through_the_log() -> None:
    """The store holds events, not dicts  -  a payload comes back as the class it went in as."""
    store, ctx = MemoryEventStore(), _ctx()
    payload = RunCompleted(output=[TextBlock(text="done")], usage=Usage(input_tokens=1, output_tokens=1))
    await store.append("s-1", [payload], ctx, ORIGIN)
    assert (await store.read("s-1", ctx))[0].payload == payload


async def test_a_run_id_already_recorded_under_one_log_key_refuses_a_write_under_another() -> None:
    """One logical run cannot be split across two log keys (#324): the same corruption
    SQLite's tightened ``events_by_run`` index refuses at the DB level, enforced here through
    the same ``_run_logs`` mapping ``locate`` already reads."""
    store, ctx = MemoryEventStore(), _ctx()
    opening = RunStarted(invocable=ORIGIN, kind_of_invocable="agent", input=[])
    await store.append("s-1", [opening], ctx, ORIGIN)  # a lifecycle write is what records _run_logs

    with pytest.raises(StoreError, match="r-1"):
        await store.append("s-2", _deltas(1), ctx, ORIGIN)
