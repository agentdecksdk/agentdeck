"""The memory event log: ordering, ranged reads, and tenant isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import Event, RunCompleted, TextDelta, Usage

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _event(seq: int, tenant: str = "acme") -> Event:
    payload = TextDelta(message_id="m1", text=f"chunk {seq}")
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id="r-1",
        session_id="s-1",
        tenant=tenant,
        origin="Greeter",
        ts=TS,
        payload=payload,
    )


def _ctx(tenant: str = "acme") -> RunContext:
    return RunContext(tenant=tenant, principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


async def test_events_read_back_in_the_order_they_were_appended() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1)], ctx)
    await store.append("s-1", [_event(2)], ctx)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0, 1, 2]


async def test_from_seq_is_inclusive_so_zero_reads_everything() -> None:
    store, ctx = MemoryEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1), _event(2)], ctx)
    assert [event.seq for event in await store.read("s-1", ctx, from_seq=0)] == [0, 1, 2]
    assert [event.seq for event in await store.read("s-1", ctx, from_seq=2)] == [2]


async def test_an_unknown_log_reads_as_empty() -> None:
    assert await MemoryEventStore().read("nobody", _ctx()) == []


async def test_one_tenant_cannot_read_another_tenants_log_under_the_same_key() -> None:
    """Two tenants are free to pick the same session id; the store keeps them apart."""
    store = MemoryEventStore()
    await store.append("s-1", [_event(0, tenant="acme")], _ctx("acme"))
    await store.append("s-1", [_event(0, tenant="globex")], _ctx("globex"))

    acme = await store.read("s-1", _ctx("acme"))
    globex = await store.read("s-1", _ctx("globex"))
    assert [event.tenant for event in acme] == ["acme"]
    assert [event.tenant for event in globex] == ["globex"]


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
        tenant="acme",
        origin="Greeter",
        ts=TS,
        payload=payload,
    )
    await store.append("s-1", [event], ctx)
    assert (await store.read("s-1", ctx))[0].payload == payload
