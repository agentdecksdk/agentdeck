"""The SQLite event log: same contract as ``tests/test_memory_store.py``, durable.

Mirrors that file's cases 1:1 for the append/read/read_run/tenancy basics, so a reviewer
can diff the two directly, plus the one thing memory cannot prove (durability across
instances). The focused queries — ``last_seq``, ``run_status``, ``list_runs``, paginated
``read`` — are asserted once for both stores in ``tests/contract/test_store.py`` instead:
SQLite answers those with its own SQL rather than the port's default, so parity there is
an invariant worth running against every store, not prose to be diffed by hand. The last
case here is the other thing memory cannot prove: two connections to one file racing a
resume claim, which is the shape two server processes are in.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    Event,
    KnownPayload,
    RunCompleted,
    RunContextSnapshot,
    RunInterrupted,
    RunResumed,
    RunStarted,
    TextDelta,
    Usage,
)

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _event(seq: int, tenant: str = "acme", run_id: str = "r-1") -> Event:
    payload = TextDelta(message_id="m1", text=f"chunk {seq}")
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id="s-1",
        tenant=tenant,
        origin="Greeter",
        ts=TS,
        payload=payload,
    )


def _ctx(tenant: str = "acme") -> RunContext:
    return RunContext(tenant=tenant, principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


def _lifecycle(seq: int, payload: KnownPayload) -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id="r-1",
        session_id="s-1",
        tenant="acme",
        origin="Approver",
        ts=TS,
        payload=payload,
    )


def _started() -> RunStarted:
    context = RunContextSnapshot(principal="user:1", trace_id="tr-1")
    return RunStarted(invocable="Approver", kind_of_invocable="workflow", input=[], context=context)


def _interrupted() -> RunInterrupted:
    return RunInterrupted(interrupt_id="i-1", reason="approval", payload={}, thread_id="t-1")


async def test_events_read_back_in_the_order_they_were_appended() -> None:
    store, ctx = SqliteEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1)], ctx)
    await store.append("s-1", [_event(2)], ctx)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0, 1, 2]


async def test_from_seq_is_inclusive_so_zero_reads_the_whole_run() -> None:
    store, ctx = SqliteEventStore(), _ctx()
    await store.append("s-1", [_event(0), _event(1), _event(2)], ctx)
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=0)] == [0, 1, 2]
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=2)] == [2]


async def test_a_seq_range_covers_one_run_and_never_splices_two() -> None:
    """``seq`` restarts at 0 per run, so a range over the whole log would return the tail of
    every run in it — which is why a range read has to name the run."""
    store, ctx = SqliteEventStore(), _ctx()
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
    assert await SqliteEventStore().read("nobody", _ctx()) == []
    assert await SqliteEventStore().read_run("nobody", "r-1", _ctx()) == []


async def test_an_event_stamped_for_another_tenant_is_refused() -> None:
    """The bucket is chosen by the context, so writing a foreign event would file it under the
    wrong tenant — the isolation has to be enforced where it is claimed."""
    store = SqliteEventStore()
    with pytest.raises(ValueError, match="globex"):
        await store.append("s-1", [_event(0, tenant="globex")], _ctx("acme"))
    assert await store.read("s-1", _ctx("acme")) == []


async def test_one_tenant_cannot_read_another_tenants_log_under_the_same_key() -> None:
    """Two tenants are free to pick the same session id; the store keeps them apart."""
    store = SqliteEventStore()
    await store.append("s-1", [_event(0, tenant="acme")], _ctx("acme"))
    await store.append("s-1", [_event(0, tenant="globex")], _ctx("globex"))

    acme = await store.read("s-1", _ctx("acme"))
    globex = await store.read("s-1", _ctx("globex"))
    assert [event.tenant for event in acme] == ["acme"]
    assert [event.tenant for event in globex] == ["globex"]


async def test_the_stub_completion_payload_round_trips_through_the_log() -> None:
    """The store holds events, not dicts — a payload comes back as the class it went in as."""
    store, ctx = SqliteEventStore(), _ctx()
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


async def test_persists_to_a_real_file_across_separate_store_instances(tmp_path) -> None:
    """The one thing memory can't prove: durability past the process holding it."""
    db_path = tmp_path / "events.sqlite3"
    ctx = _ctx()
    await SqliteEventStore(db_path).append("s-1", [_event(0), _event(1)], ctx)
    reopened = SqliteEventStore(db_path)
    assert [event.seq for event in await reopened.read("s-1", ctx)] == [0, 1]


async def test_two_connections_to_one_file_settle_a_resume_claim_on_one_winner(tmp_path) -> None:
    """Two stores over one file share no lock, no cache and no ``asyncio.Lock`` — the same
    position two server processes are in. Repeated, because a broken transaction boundary
    shows up as an occasional second winner rather than a consistent one.
    """
    for trial in range(20):
        db_path = tmp_path / f"race-{trial}.sqlite3"
        ctx = _ctx()
        seeder = SqliteEventStore(db_path)
        await seeder.append("s-1", [_lifecycle(0, _started()), _lifecycle(1, _interrupted())], ctx)
        seeder.close()

        first, second = SqliteEventStore(db_path), SqliteEventStore(db_path)
        claim = _lifecycle(2, RunResumed(reason=None))
        try:
            outcomes = await asyncio.gather(
                first.claim_resume("s-1", "r-1", claim, ctx), second.claim_resume("s-1", "r-1", claim, ctx)
            )
            assert sorted(outcomes) == [False, True], f"trial {trial}: {outcomes}"
            stored = await first.read_run("s-1", "r-1", ctx)
        finally:
            first.close()
            second.close()

        assert [event.kind for event in stored] == ["run.started", "run.interrupted", "run.resumed"]
        assert [event.seq for event in stored] == [0, 1, 2]
