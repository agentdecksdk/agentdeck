"""The memory event log: ordering, ranged reads, and namespace isolation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import Event, RunCompleted, TextDelta, Usage

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _event(seq: int, namespace: str = "acme", run_id: str = "r-1") -> Event:
    payload = TextDelta(message_id="m1", text=f"chunk {seq}")
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id="s-1",
        namespace=namespace,
        origin="Greeter",
        ts=TS,
        payload=payload,
    )


def _ctx(namespace: str = "acme") -> RunContext:
    return RunContext(namespace=namespace, run_id="r-1", trace_id="tr-1", session_id="s-1")


async def test_events_read_back_in_the_order_they_were_appended() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1)], ctx)
    await store.append("s-1", [_event(2)], ctx)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0, 1, 2]


async def test_from_seq_is_inclusive_so_zero_reads_the_whole_run() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1), _event(2)], ctx)
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=0)] == [0, 1, 2]
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=2)] == [2]


async def test_a_seq_range_covers_one_run_and_never_splices_two() -> None:
    """``seq`` restarts at 0 per run, so a range over the whole log would return the tail of
    every run in it — which is why a range read has to name the run."""
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0, run_id="r-1"), _event(1, run_id="r-1")], ctx)
    await store.append("s-1", [_event(0, run_id="r-2"), _event(1, run_id="r-2")], ctx)

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


async def test_an_event_stamped_for_another_tenant_is_refused() -> None:
    """The bucket is chosen by the context, so writing a foreign event would file it under the
    wrong namespace — the isolation has to be enforced where it is claimed."""
    store = MemoryEventStore()
    with pytest.raises(ValueError, match="globex"):
        await store.append("s-1", [_event(0, namespace="globex")], _ctx("acme"))
    assert await store.read("s-1", _ctx("acme")) == []


async def test_one_tenant_cannot_read_another_tenants_log_under_the_same_key() -> None:
    """Two tenants are free to pick the same session id; the store keeps them apart."""
    store = MemoryEventStore()
    await store.append("s-1", [_event(0, namespace="acme")], _ctx("acme"))
    await store.append("s-1", [_event(0, namespace="globex")], _ctx("globex"))

    acme = await store.read("s-1", _ctx("acme"))
    globex = await store.read("s-1", _ctx("globex"))
    assert [event.namespace for event in acme] == ["acme"]
    assert [event.namespace for event in globex] == ["globex"]


async def test_a_read_cannot_be_used_to_mutate_the_log() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0)], ctx)
    (await store.read("s-1", ctx)).append(_event(99))
    assert [event.seq for event in await store.read("s-1", ctx)] == [0]


async def test_the_stub_completion_payload_round_trips_through_the_log() -> None:
    """The store holds events, not dicts — a payload comes back as the class it went in as."""
    store, ctx = MemoryEventStore(), _ctx()
    payload = RunCompleted(output=[TextBlock(text="done")], usage=Usage(input_tokens=1, output_tokens=1))
    event = Event(
        kind=payload.kind,
        seq=0,
        run_id="r-1",
        session_id="s-1",
        namespace="acme",
        origin="Greeter",
        ts=TS,
        payload=payload,
    )
    await store.append("s-1", [event], ctx)
    assert (await store.read("s-1", ctx))[0].payload == payload
