"""Store contract: the focused queries — ``last_seq``, ``run_status``, ``list_runs`` and
paginated ``read`` — behave identically on every store, parametrized the same way the
engine cases are. Ordering/tenancy/round-trip invariants for ``append``, ``read`` and
``read_run`` already live in ``tests/test_memory_store.py`` and ``tests/test_sqlite_store.py``;
this file covers only the newer focused ops.

Parametrized over all four stores: memory, SQLite, and — on real servers, skipping with a
reason when there is none — Redis and Postgres (``live_stores``). Backend-specific evidence
that needs no second store lives beside each one instead: ``tests/test_sqlite_store.py``,
``tests/test_redis_store.py``, ``tests/test_postgres_store.py``.

The last case is a boundary invariant rather than a query one, and covers both SQLite-backed
ports by shape: whatever fails underneath, callers see the harness's own error type.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any

import live_stores
import pytest

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.adapters.stores.sqlite import store as sqlite_store
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import (
    Event,
    KnownPayload,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    RunPaused,
    RunResumed,
    RunStarted,
    TextDelta,
)
from agentdeck.core.ports import SessionClaim
from agentdeck.core.status import RunStatus
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
    from pathlib import Path

    from agentdeck.core.ports import EventStorePort

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(params=live_stores.BACKENDS)
async def event_store(request: pytest.FixtureRequest) -> AsyncIterator[EventStorePort]:
    """Every case against every store — including Redis and Postgres on real servers, which
    skip with a reason naming the env var when there is none (``live_stores``)."""
    async with live_stores.event_store(request.param) as store:
        yield store


@pytest.fixture(params=live_stores.BACKENDS)
async def two_event_stores(request: pytest.FixtureRequest) -> AsyncIterator[tuple[EventStorePort, EventStorePort]]:
    """Two handles on one keyspace, for the promises that only hold between two writers."""
    async with live_stores.two_event_stores(request.param) as pair:
        yield pair


def _ctx(tenant: str = "acme") -> RunContext:
    return RunContext(tenant=tenant, principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


def _event(
    seq: int,
    payload: KnownPayload,
    tenant: str = "acme",
    run_id: str = "r-1",
    log_key: str = "s-1",
    ts: datetime = TS,
) -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id=log_key,
        tenant=tenant,
        origin="Greeter",
        ts=ts,
        payload=payload,
    )


def _started() -> RunStarted:
    return RunStarted(
        invocable="Greeter",
        kind_of_invocable="agent",
        input=[],
        context={"principal": "user:1", "trace_id": "tr-1"},
    )


async def test_last_seq_is_negative_one_for_a_run_with_no_events(event_store: EventStorePort) -> None:
    assert await event_store.last_seq("s-1", "r-1", _ctx()) == -1


async def test_last_seq_tracks_the_highest_seq_appended_for_that_run(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started()), _event(1, TextDelta(message_id="m1", text="hi"))], ctx)
    assert await event_store.last_seq("s-1", "r-1", ctx) == 1


async def test_last_seq_is_scoped_to_one_run_not_the_whole_log(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await event_store.append("s-1", [_event(0, _started(), run_id="r-2"), _event(1, _started(), run_id="r-2")], ctx)
    assert await event_store.last_seq("s-1", "r-1", ctx) == 0
    assert await event_store.last_seq("s-1", "r-2", ctx) == 1
    assert await event_store.last_seq("s-1", "r-3", ctx) == -1


async def test_run_status_with_no_events_is_pending(event_store: EventStorePort) -> None:
    assert await event_store.run_status("s-1", "r-1", _ctx()) is RunStatus.PENDING


async def test_run_status_follows_the_last_lifecycle_transition(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append(
        "s-1",
        [
            _event(0, _started()),
            _event(1, RunInterrupted(interrupt_id="i-1", reason="human", payload={"q": "ok?"}, thread_id="t-1")),
        ],
        ctx,
    )
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.WAITING_HUMAN


async def test_run_status_is_scoped_to_one_run_not_the_whole_log(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await event_store.append(
        "s-1",
        [
            _event(0, _started(), run_id="r-2"),
            _event(1, RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1}), run_id="r-2"),
        ],
        ctx,
    )
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.RUNNING
    assert await event_store.run_status("s-1", "r-2", ctx) is RunStatus.COMPLETED


# Cutoffs on either side of every event these tests write, so staleness is decided by the
# argument and never by how long the test itself took.
BEFORE_ANY_EVENT = TS - timedelta(hours=1)
AFTER_EVERY_EVENT = TS + timedelta(hours=1)


def _opening(run_id: str = "r-1", log_key: str = "s-1", tenant: str = "acme", ts: datetime = TS) -> Event:
    return _event(0, _started(), tenant=tenant, run_id=run_id, log_key=log_key, ts=ts)


async def test_claim_start_opens_a_run_on_an_idle_session(event_store: EventStorePort) -> None:
    ctx = _ctx()
    assert await event_store.claim_start("s-1", _opening(), ctx, BEFORE_ANY_EVENT) == SessionClaim()
    assert [event.kind for event in await event_store.read("s-1", ctx)] == ["run.started"]
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.RUNNING


async def test_claim_start_refuses_a_session_that_already_has_a_run_going(event_store: EventStorePort) -> None:
    """The refusal names the run holding the session, and writes nothing: one turn per session,
    decided by the same write that would have opened the second one."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening()], ctx)

    assert await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, BEFORE_ANY_EVENT) == SessionClaim(
        held_by="r-1"
    )
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1"]


async def test_claim_start_refuses_a_session_whose_run_is_waiting_on_a_human(event_store: EventStorePort) -> None:
    """``WAITING_HUMAN`` is not free: the interrupted run still owns its engine's thread, and a
    second run against it would write over the checkpoints that run resumes from."""
    ctx = _ctx()
    await _interrupt(event_store, ctx)

    claim = await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, BEFORE_ANY_EVENT)
    assert claim == SessionClaim(held_by="r-1")
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1", "r-1"]


@pytest.mark.parametrize(
    "closing",
    [
        pytest.param(RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1}), id="completed"),
        pytest.param(RunFailed(error_code="engine_error", message="boom", retryable=False), id="failed"),
        pytest.param(RunCancelled(reason="consumer stopped reading"), id="cancelled"),
    ],
)
async def test_claim_start_wins_once_the_previous_run_is_closed(
    event_store: EventStorePort, closing: KnownPayload
) -> None:
    """Every terminal event frees the session — a turn after a failed or cancelled one is the
    ordinary case, not a special one."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening(), _event(1, closing)], ctx)

    assert await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, BEFORE_ANY_EVENT) == SessionClaim()
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1", "r-1", "r-2"]


async def test_a_run_that_recorded_no_transition_holds_no_session(event_store: EventStorePort) -> None:
    """``PENDING`` is indistinguishable from a run the store never saw, so it cannot hold
    anything — the same line ``list_runs`` draws."""
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, TextDelta(message_id="m1", text="hi"), run_id="r-0")], ctx)

    assert await event_store.claim_start("s-1", _opening(), ctx, BEFORE_ANY_EVENT) == SessionClaim()


async def test_claim_start_is_scoped_to_one_log_not_the_whole_store(event_store: EventStorePort) -> None:
    """A run going in one session says nothing about another: sessions are the unit that runs
    one turn at a time."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening()], ctx)

    assert await event_store.claim_start("s-2", _opening(run_id="r-2", log_key="s-2"), ctx, BEFORE_ANY_EVENT) == (
        SessionClaim()
    )


async def test_claim_start_never_sees_another_tenants_open_run(event_store: EventStorePort) -> None:
    """Two tenants are free to pick the same session id; neither may hold the other's session,
    and an event stamped for a foreign tenant never lands in this log."""
    await event_store.append("s-1", [_opening()], _ctx("acme"))
    intruder = _ctx("globex")

    assert await event_store.claim_start(
        "s-1", _opening(run_id="r-9", tenant="globex"), intruder, BEFORE_ANY_EVENT
    ) == (SessionClaim())
    with pytest.raises(ValueError, match="acme"):
        await event_store.claim_start("s-1", _opening(run_id="r-8"), intruder, BEFORE_ANY_EVENT)


async def test_a_run_silent_past_the_cutoff_stops_holding_its_session(event_store: EventStorePort) -> None:
    """The hard-kill case: a process that died leaves a run nothing will ever close, so an open
    run that has gone quiet long enough is stepped over — and reported, because closing it means
    stamping an event, which is the caller's job and not a store's."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening()], ctx)

    claim = await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, AFTER_EVERY_EVENT)
    assert claim == SessionClaim(overridden=("r-1",))
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1", "r-2"]
    assert [event.kind for event in await event_store.read_run("s-1", "r-1", ctx)] == ["run.started"]


async def test_staleness_is_measured_from_the_last_event_of_a_run_not_its_last_transition(
    event_store: EventStorePort,
) -> None:
    """A run streaming for hours has an old ``run.started`` and a very recent delta. Judging it
    by the transition would take a working turn's session away from it mid-stream."""
    ctx = _ctx()
    recent = _event(1, TextDelta(message_id="m1", text="still here"), ts=AFTER_EVERY_EVENT)
    await event_store.append("s-1", [_opening(), recent], ctx)

    claim = await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, TS + timedelta(minutes=1))
    assert claim == SessionClaim(held_by="r-1")
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1", "r-1"]


async def test_one_live_run_refuses_a_claim_even_beside_an_abandoned_one(event_store: EventStorePort) -> None:
    """A refused claim overrides nothing: a session with one dead run and one live one is busy,
    and stepping over the dead one anyway would leave a takeover half-done."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening(run_id="r-dead")], ctx)
    await event_store.append("s-1", [_opening(run_id="r-live", ts=AFTER_EVERY_EVENT)], ctx)

    claim = await event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, TS + timedelta(minutes=1))
    assert claim == SessionClaim(held_by="r-live")
    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-dead", "r-live"]


async def test_two_claims_gathered_on_one_session_have_exactly_one_winner(event_store: EventStorePort) -> None:
    """The claim's own atomicity, not the Runtime's use of it: an ``await`` slipped between the
    scan and the append would let both of these find the session idle, and every other test here
    would still pass."""
    ctx = _ctx()
    claims = await asyncio.gather(
        event_store.claim_start("s-1", _opening(run_id="r-1"), ctx, BEFORE_ANY_EVENT),
        event_store.claim_start("s-1", _opening(run_id="r-2"), ctx, BEFORE_ANY_EVENT),
    )

    assert [claim.held_by is None for claim in claims].count(True) == 1, claims
    assert len(await event_store.read("s-1", ctx)) == 1


async def test_one_seq_per_run_is_refused_a_second_time(event_store: EventStorePort) -> None:
    """The corruption a gap check cannot see. Two writers only ever share a run when one of them
    was presumed dead and came back, and the second event at that ``seq`` would make every
    consumer's refetch of it a coin toss — so the store refuses it, and says so as a
    ``StoreError`` rather than a silent write."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening()], ctx)

    with pytest.raises(StoreError):
        await event_store.append("s-1", [_event(0, TextDelta(message_id="m1", text="not yours"))], ctx)
    assert [event.kind for event in await event_store.read("s-1", ctx)] == ["run.started"]


async def test_a_batch_holding_its_own_duplicate_seq_is_refused_whole(event_store: EventStorePort) -> None:
    """All or nothing: a rejected batch must not leave the events before the duplicate behind,
    or the log would hold a partial write nobody asked for."""
    ctx = _ctx()
    delta = TextDelta(message_id="m1", text="hi")
    with pytest.raises(StoreError):
        await event_store.append("s-1", [_opening(), _event(1, delta), _event(1, delta)], ctx)
    assert await event_store.read("s-1", ctx) == []


async def test_one_seq_per_run_does_not_stop_two_runs_sharing_a_seq_in_one_log(
    event_store: EventStorePort,
) -> None:
    """``seq`` is per run, so every run in a session log counts from 0 — the constraint is on the
    pair, and a store that keyed it on the log alone would refuse the second run's opening event."""
    ctx = _ctx()
    await event_store.append("s-1", [_opening(run_id="r-1")], ctx)
    await event_store.append("s-1", [_opening(run_id="r-2")], ctx)

    assert [event.run_id for event in await event_store.read("s-1", ctx)] == ["r-1", "r-2"]
    assert [event.seq for event in await event_store.read("s-1", ctx)] == [0, 0]


async def test_one_seq_per_run_holds_on_the_claim_paths_too(event_store: EventStorePort) -> None:
    """The same guarantee, asked of ``claim_start`` rather than ``append``.

    A conditional append is still an append, and its refusal of a *busy session* is a different
    answer from its refusal of a *spent seq*: the first is data (``held_by``), the second is a
    store error, because a duplicate is corruption rather than a race somebody lost. A store
    that enforced one seq per run only on the plain path would let this one through, and the log
    would hold two different events at ``(r-1, 0)`` with nothing to reveal it — a gap check sees
    no hole, and ``last_seq`` reads the same either way.
    """
    ctx = _ctx()
    closing = RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1})
    await event_store.append("s-1", [_opening(run_id="r-1"), _event(1, closing, run_id="r-1")], ctx)

    with pytest.raises(StoreError):
        await event_store.claim_start("s-1", _opening(run_id="r-1"), ctx, BEFORE_ANY_EVENT)
    assert [(event.run_id, event.seq) for event in await event_store.read("s-1", ctx)] == [("r-1", 0), ("r-1", 1)]


async def _interrupt(event_store: EventStorePort, ctx: RunContext, run_id: str = "r-1") -> None:
    """Leave one run parked in ``WAITING_HUMAN`` — the only status a resume may claim."""
    interrupted = RunInterrupted(interrupt_id="i-1", reason="human", payload={"q": "ok?"}, thread_id="t-1")
    await event_store.append("s-1", [_event(0, _started(), run_id=run_id), _event(1, interrupted, run_id=run_id)], ctx)


def _resumed(seq: int, run_id: str = "r-1") -> Event:
    return _event(seq, RunResumed(reason=None), run_id=run_id)


async def test_claim_resume_appends_the_event_and_wins_when_the_run_is_waiting(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await _interrupt(event_store, ctx)

    assert await event_store.claim_resume("s-1", "r-1", _resumed(2), ctx) is True
    assert [event.kind for event in await event_store.read_run("s-1", "r-1", ctx)][-1] == "run.resumed"
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.RUNNING


async def test_a_second_claim_on_the_same_run_loses_and_writes_nothing(event_store: EventStorePort) -> None:
    """The invariant double-resume protection rests on: the check and the append are one
    step, so the loser cannot append a second ``run.resumed`` or reuse the winner's seq."""
    ctx = _ctx()
    await _interrupt(event_store, ctx)
    assert await event_store.claim_resume("s-1", "r-1", _resumed(2), ctx) is True

    assert await event_store.claim_resume("s-1", "r-1", _resumed(2), ctx) is False
    stored = await event_store.read_run("s-1", "r-1", ctx)
    assert [event.kind for event in stored].count("run.resumed") == 1
    assert [event.seq for event in stored] == [0, 1, 2]


async def test_claim_resume_wins_on_a_paused_run_too(event_store: EventStorePort) -> None:
    """``PAUSED`` is the other *suspended* status, and one claim serves both: a paused run is
    owed a terminal event just as a parked approval is, and only one caller may continue it.

    This is the positive half of the guard below — without it, a store could refuse every
    status but ``WAITING_HUMAN`` and no test would notice that pause had stopped resuming.
    """
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started()), _event(1, RunPaused(reason="operator"))], ctx)
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.PAUSED

    assert await event_store.claim_resume("s-1", "r-1", _resumed(2), ctx) is True
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.RUNNING


@pytest.mark.parametrize("kind", ["pending", "running", "completed", "cancelled"])
async def test_claim_resume_refuses_a_run_that_is_not_suspended(event_store: EventStorePort, kind: str) -> None:
    """A resume against any status that is not suspended is a no-op, not an error — including a
    run this store has never heard of, which is indistinguishable from one that never started.

    ``cancelled`` is the one that carries a promise rather than just a rule: cancel is terminal,
    so this guard is what makes "a cancelled run cannot be resumed" true across processes, where
    a caller's own status check could always go stale between reading and appending.

    The claimed event carries the run's *real* next ``seq``, read from the store. A claim with
    the wrong seq is refused whatever the status, so hardcoding one would let this pass on the
    seq guard and assert nothing about status at all.
    """
    ctx = _ctx()
    if kind == "running":
        await event_store.append("s-1", [_event(0, _started())], ctx)
    elif kind == "completed":
        await event_store.append(
            "s-1",
            [_event(0, _started()), _event(1, RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1}))],
            ctx,
        )
    elif kind == "cancelled":
        await event_store.append(
            "s-1", [_event(0, _started()), _event(1, RunCancelled(reason="user closed the tab"))], ctx
        )
    next_seq = await event_store.last_seq("s-1", "r-1", ctx) + 1

    assert await event_store.claim_resume("s-1", "r-1", _resumed(next_seq), ctx) is False
    assert [event.kind for event in await event_store.read_run("s-1", "r-1", ctx)].count("run.resumed") == 0


async def test_a_claim_carrying_a_stale_seq_loses_even_though_the_run_is_waiting_again(
    event_store: EventStorePort,
) -> None:
    """A caller stamps its ``run.resumed`` before claiming. If the run is resumed and
    interrupted again in between, status alone would wave that claim through — and it would
    write a ``seq`` the winner already used, silently: nothing else in the log would object.
    """
    ctx = _ctx()
    await _interrupt(event_store, ctx)
    stale = _resumed(2)
    assert await event_store.claim_resume("s-1", "r-1", stale, ctx) is True

    # the winner's run asked a second question, so the run is WAITING_HUMAN once more
    again = RunInterrupted(interrupt_id="i-2", reason="human", payload={"q": "and this?"}, thread_id="t-1")
    await event_store.append("s-1", [_event(3, again)], ctx)
    assert await event_store.run_status("s-1", "r-1", ctx) is RunStatus.WAITING_HUMAN

    assert await event_store.claim_resume("s-1", "r-1", stale, ctx) is False
    stored = await event_store.read_run("s-1", "r-1", ctx)
    assert [event.seq for event in stored] == [0, 1, 2, 3]
    assert len({event.seq for event in stored}) == len(stored)

    assert await event_store.claim_resume("s-1", "r-1", _resumed(4), ctx) is True  # a current seq still wins


async def test_a_claim_must_carry_an_event_for_the_run_it_names(event_store: EventStorePort) -> None:
    """The status is checked for ``run_id`` and the event is filed under its own — a caller
    passing two different runs would have the store answer about one and write the other."""
    ctx = _ctx()
    await _interrupt(event_store, ctx)
    with pytest.raises(ValueError, match="r-2"):
        await event_store.claim_resume("s-1", "r-1", _resumed(2, run_id="r-2"), ctx)


async def test_claim_resume_is_scoped_to_one_run_not_the_whole_log(event_store: EventStorePort) -> None:
    """One waiting run in a log must not license a resume of a different run beside it."""
    ctx = _ctx()
    await _interrupt(event_store, ctx, run_id="r-1")
    await event_store.append("s-1", [_event(0, _started(), run_id="r-2")], ctx)

    assert await event_store.claim_resume("s-1", "r-2", _resumed(1, run_id="r-2"), ctx) is False
    assert await event_store.claim_resume("s-1", "r-1", _resumed(2), ctx) is True


async def test_claim_resume_never_reaches_into_another_tenants_waiting_run(event_store: EventStorePort) -> None:
    """Same isolation as every other query: another tenant's interrupt is not claimable, and
    an event stamped for a foreign tenant never lands in this log."""
    await _interrupt(event_store, _ctx("acme"))
    intruder = _ctx("globex")

    own = _event(2, RunResumed(reason=None), tenant="globex")
    assert await event_store.claim_resume("s-1", "r-1", own, intruder) is False
    with pytest.raises(ValueError, match="acme"):
        await event_store.claim_resume("s-1", "r-1", _resumed(2), intruder)
    assert [event.kind for event in await event_store.read_run("s-1", "r-1", _ctx("acme"))].count("run.resumed") == 0


async def test_list_runs_scopes_to_one_tenant(event_store: EventStorePort) -> None:
    await event_store.append("s-1", [_event(0, _started(), tenant="acme")], _ctx("acme"))
    await event_store.append("s-1", [_event(0, _started(), tenant="globex")], _ctx("globex"))

    acme_runs = await event_store.list_runs(_ctx("acme"))
    assert [summary.run_id for summary in acme_runs] == ["r-1"]


async def test_list_runs_filters_by_status(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await event_store.append(
        "s-1",
        [
            _event(0, _started(), run_id="r-2"),
            _event(1, RunInterrupted(interrupt_id="i-1", reason="human", payload={}, thread_id="t-2"), run_id="r-2"),
        ],
        ctx,
    )

    waiting = await event_store.list_runs(ctx, status=RunStatus.WAITING_HUMAN)
    assert [(summary.run_id, summary.status) for summary in waiting] == [("r-2", RunStatus.WAITING_HUMAN)]

    everyone = await event_store.list_runs(ctx)
    assert {summary.run_id for summary in everyone} == {"r-1", "r-2"}


async def test_list_runs_of_an_empty_store_is_empty(event_store: EventStorePort) -> None:
    assert await event_store.list_runs(_ctx()) == []


async def test_list_runs_enumerates_runs_across_every_log_key_of_the_tenant(event_store: EventStorePort) -> None:
    """A tenant's waiting runs live in as many logs as it has sessions — a listing that only
    looked in one log key would silently hide every other session's interrupts."""
    ctx = _ctx()
    interrupted = RunInterrupted(interrupt_id="i-1", reason="human", payload={}, thread_id=None)
    await event_store.append("s-1", [_event(0, _started(), run_id="r-1"), _event(1, interrupted, run_id="r-1")], ctx)
    await event_store.append(
        "s-2",
        [_event(0, _started(), run_id="r-2", log_key="s-2"), _event(1, interrupted, run_id="r-2", log_key="s-2")],
        ctx,
    )

    waiting = await event_store.list_runs(ctx, status=RunStatus.WAITING_HUMAN)
    assert {(summary.log_key, summary.run_id) for summary in waiting} == {("s-1", "r-1"), ("s-2", "r-2")}


async def test_list_runs_skips_a_run_whose_log_holds_no_lifecycle_event(event_store: EventStorePort) -> None:
    """Such a run is ``PENDING``, which no listing can tell apart from a run the store never
    saw — both stores leave it out rather than one inventing it."""
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, TextDelta(message_id="m1", text="hi"))], ctx)
    assert await event_store.list_runs(ctx) == []


async def test_paginated_read_offset_skips_the_first_n_events(event_store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await event_store.append("s-1", events, ctx)
    page = await event_store.read("s-1", ctx, offset=2)
    assert [event.seq for event in page] == [2, 3, 4]


async def test_paginated_read_limit_caps_the_page(event_store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await event_store.append("s-1", events, ctx)
    page = await event_store.read("s-1", ctx, limit=2)
    assert [event.seq for event in page] == [0, 1]


async def test_paginated_read_offset_and_limit_compose_into_the_next_page(event_store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await event_store.append("s-1", events, ctx)
    page = await event_store.read("s-1", ctx, offset=2, limit=2)
    assert [event.seq for event in page] == [2, 3]


async def test_paginated_read_past_the_end_is_empty(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started())], ctx)
    assert await event_store.read("s-1", ctx, offset=10) == []


async def test_a_page_already_read_does_not_shift_when_the_log_grows(event_store: EventStorePort) -> None:
    """The promise paging rests on: a log only ever grows at its end, so an offset a reader
    has passed keeps meaning the same event. A store that ordered by anything a later write
    can slot in front of — or that published its ordering out of the order it assigned it —
    would move an unread event behind the cursor and deliver its neighbour twice.

    Sequential here, which is all one store instance can show, and it passes on all four with
    the concurrent half of the promise broken: that half — a write committing while another
    writer's batch is still in flight — is the case below, on two handles.
    """
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started()), _event(1, RunResumed(reason=None))], ctx)
    first_page = await event_store.read("s-1", ctx, offset=0, limit=2)

    await event_store.append("s-1", [_event(2, TextDelta(message_id="m1", text="later"))], ctx)

    assert await event_store.read("s-1", ctx, offset=0, limit=2) == first_page
    assert [event.seq for event in await event_store.read("s-1", ctx, offset=2)] == [2]


# Wide enough that inserting it takes far longer than the peer's round trip, so the peer writes
# while it is still open. That margin is what reproduces the hazard, and it is a margin and not
# a guarantee: nothing here forces the peer's write to be numbered inside the batch. A machine
# that closes the gap loses the hazard, which is why the report below says whether it happened —
# a lost hazard must never read as a passing store.
_INTERLEAVED_BATCH = 200

# Five peer writes rather than one, spread through that window instead of betting on one moment.
# Cheap insurance rather than a fix for anything measured: one write reproduces the hazard too.
_TRAILING_WRITES = 5

# A bound on a broken run, never part of an assertion: a reader that has lost an event to a
# shift will never reach its count, and has to stop asking at some point.
_PAGING_DEADLINE = 10.0


def _keys(events: Iterable[Event]) -> list[tuple[str, int]]:
    """Each event as its identity — one ``(run, seq)`` per event, ever."""
    return [(event.run_id, event.seq) for event in events]


async def _append_each(store: EventStorePort, ctx: RunContext, events: Iterable[Event]) -> None:
    """One committed write per event, so a run of them straddles whatever a peer has open."""
    for event in events:
        await store.append("s-1", [event], ctx)


async def _page_the_log(store: EventStorePort, ctx: RunContext, until: int) -> list[list[Event]]:
    """Page a log the way the port says is safe — a plain counter as the cursor — until
    ``until`` events have come back or the deadline gives up on them.

    The cursor being the count delivered so far is the whole point: an event that lands
    *behind* it is never delivered, and whatever it displaced is delivered twice. Returns the
    pages rather than the events, because how the delivery was split across them is the
    evidence that anything was read while the log was still being written.
    """
    pages: list[list[Event]] = []
    delivered = 0
    deadline = monotonic() + _PAGING_DEADLINE
    while delivered < until and monotonic() < deadline:
        page = await store.read("s-1", ctx, offset=delivered)
        if not page:
            await asyncio.sleep(0.001)  # nothing new committed yet, so let the writer get on with it
            continue
        pages.append(page)
        delivered += len(page)
    return pages


async def test_a_page_already_read_does_not_shift_when_a_second_writer_commits(
    two_event_stores: tuple[EventStorePort, EventStorePort],
) -> None:
    """The promise above in the shape one instance cannot show: a second writer commits while
    the first's batch is still in flight, and a reader pages across both.

    Every event is delivered exactly once and in one order, or paging is not safe to do with a
    counter. What can break it is a store that orders by a number assigned at insert and
    published at commit — Postgres's ``BIGSERIAL`` — because the peer's row can be given a
    *later* number and still be published *first*: the reader takes it at an offset it then
    leaves behind, so the batch's first event is never delivered and the peer's arrives twice.
    Serializing a log's writes is what keeps it growing only at its end.

    Memory and SQLite pass this by construction rather than by luck, and are here because a
    backend added later gets asked the same question before it is trusted.

    The interleave is arranged with a margin, not forced: the batch takes far longer to insert
    than the peer takes to commit, which is why the peer lands inside it. So the report at the
    end says how the delivery was split — a machine that closes that margin delivers the whole
    settled log in one page, and this case would then pass without having asked anything.
    """
    batching, peer = two_event_stores
    ctx = _ctx()
    batch = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(_INTERLEAVED_BATCH)]
    # A run of their own: a seq the batch also holds would be refused, and prove the unique
    # index works rather than anything about the log's order.
    trailing = [_event(seq, TextDelta(message_id="m2", text=str(seq)), run_id="r-2") for seq in range(_TRAILING_WRITES)]
    # Both handles connected and set up before the race — a cold one spends its first call
    # creating a schema, which is a lap the other does not run.
    await batching.read("s-1", ctx)
    await peer.read("s-1", ctx)

    writing = asyncio.create_task(batching.append("s-1", batch, ctx))
    await asyncio.sleep(0)  # into its write before the peer opens one, so the peer's rows are numbered behind it
    behind = asyncio.create_task(_append_each(peer, ctx, trailing))

    # Reads go through the writing peer's own handle, so every page is taken between two of its
    # commits — the moment a shift would be visible in — and never inside one.
    pages = await _page_the_log(peer, ctx, until=len(batch) + len(trailing))
    # Bounded like the reader: a wedged writer must fail this case, not hang the suite in a
    # gather nothing ever returns from.
    async with asyncio.timeout(_PAGING_DEADLINE):
        await asyncio.gather(writing, behind)
    settled = _keys(await peer.read("s-1", ctx))

    seen = _keys(event for page in pages for event in page)

    # Reported, not asserted, for the same reason the resume race reports its overlap: the split
    # is this machine's timing. A store that keeps the promise holds the peer's writes behind the
    # open batch, so delivering them last *is* the promise being kept, and the broken one shows
    # the opposite — the peer's writes first, at an offset the batch then takes. What the report
    # is for is neither: one page holding the settled log means the batch was never open long
    # enough to be interleaved with, and the case passed without asking anything. Printed before
    # the assertions, so a failing run carries it too.
    first_trailing = next((offset for offset, (run_id, _) in enumerate(seen) if run_id == "r-2"), None)
    print(
        f"interleaved paging on {type(peer).__name__}: pages {[len(page) for page in pages]}, "
        f"the peer's first write delivered at offset {first_trailing} of {len(seen)}"
    )

    twice = [key for key, times in Counter(seen).items() if times > 1]
    never = [key for key in settled if key not in set(seen)]
    assert not twice and not never, (
        f"paging a log of {len(settled)} delivered {len(seen)}: {twice} twice, {never} never — "
        "a write landed behind the reader's cursor"
    )
    assert seen == settled, "the reader's order is not the order the log settled into"


async def test_paginated_read_zero_limit_is_an_empty_page(event_store: EventStorePort) -> None:
    ctx = _ctx()
    await event_store.append("s-1", [_event(0, _started())], ctx)
    assert await event_store.read("s-1", ctx, limit=0) == []


async def test_a_negative_offset_reads_from_the_start_and_a_negative_limit_is_refused(
    event_store: EventStorePort,
) -> None:
    """Left to the underlying store these mean opposite things — a Python slice counts back
    from the end, SQLite reads a negative LIMIT as "no limit" — so the port pins both."""
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(3)]
    await event_store.append("s-1", events, ctx)

    assert [event.seq for event in await event_store.read("s-1", ctx, offset=-2)] == [0, 1, 2]
    with pytest.raises(ValueError, match="limit"):
        await event_store.read("s-1", ctx, limit=-1)


async def test_the_focused_queries_never_answer_from_another_tenants_log(event_store: EventStorePort) -> None:
    """One tenant's populated log must read as untouched emptiness to another — the same
    isolation ``read``/``read_run`` already promise, on the queries that skip them."""
    await event_store.append(
        "s-1",
        [
            _event(0, _started(), tenant="acme"),
            _event(1, RunInterrupted(interrupt_id="i-1", reason="human", payload={}, thread_id=None), tenant="acme"),
        ],
        _ctx("acme"),
    )
    intruder = _ctx("globex")

    assert await event_store.last_seq("s-1", "r-1", intruder) == -1
    assert await event_store.run_status("s-1", "r-1", intruder) is RunStatus.PENDING
    assert await event_store.list_runs(intruder) == []
    assert await event_store.read("s-1", intruder, offset=0) == []


# Every public method of both SQLite-backed ports, so a method added later without the
# boundary wrapper is a missing case here rather than a silent leak.
_SQLITE_CALLS = [
    pytest.param(SqliteEventStore, lambda port: port.append("s-1", [_event(0, _started())], _ctx()), id="append"),
    pytest.param(SqliteEventStore, lambda port: port.read("s-1", _ctx()), id="read"),
    pytest.param(SqliteEventStore, lambda port: port.read_run("s-1", "r-1", _ctx()), id="read_run"),
    pytest.param(SqliteEventStore, lambda port: port.last_seq("s-1", "r-1", _ctx()), id="last_seq"),
    pytest.param(SqliteEventStore, lambda port: port.run_status("s-1", "r-1", _ctx()), id="run_status"),
    pytest.param(SqliteEventStore, lambda port: port.claim_resume("s-1", "r-1", _resumed(2), _ctx()), id="claim"),
    pytest.param(
        SqliteEventStore,
        lambda port: port.claim_start("s-1", _opening(), _ctx(), BEFORE_ANY_EVENT),
        id="claim_start",
    ),
    pytest.param(SqliteEventStore, lambda port: port.list_runs(_ctx()), id="list_runs"),
    pytest.param(SqliteControlPort, lambda port: port.signal("r-1", Signal.CANCEL), id="signal"),
    pytest.param(SqliteControlPort, lambda port: port.poll("r-1"), id="poll"),
]


@pytest.mark.parametrize(("port_type", "call"), _SQLITE_CALLS)
async def test_a_failed_statement_reaches_the_caller_as_a_store_error(
    port_type: Callable[[], Any], call: Callable[[Any], Coroutine[Any, Any, object]]
) -> None:
    """A ``sqlite3`` exception is a library type and must not cross a port: callers of either
    SQLite-backed port catch ``StoreError``, with the original kept only as the cause.

    ``claim_resume`` is the case that makes this load-bearing — it promises a loser a clean
    ``False``, so an unreachable store has to be distinguishable from a claim somebody won.
    Forced by closing the connection, which fails whichever statement the method reaches for:
    the shape of a database gone unreadable mid-run, without waiting out a real lock.
    """
    port = port_type()
    port.close()

    with pytest.raises(StoreError) as raised:
        await call(port)
    assert isinstance(raised.value.__cause__, sqlite3.Error)
    assert not isinstance(raised.value, sqlite3.Error)


async def test_a_write_lock_held_past_the_busy_timeout_is_a_store_error_not_a_lost_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that motivated the wrapping, in its own shape rather than a closed
    connection's: a peer holds the file's write lock longer than this store will wait for it.

    ``claim_resume`` must not answer that with ``False``. ``False`` means somebody else won and
    the resume is already recorded, so returning it here would discard a human's approval while
    reporting a race that never happened. The timeout is shortened so the wait costs milliseconds.
    """
    monkeypatch.setattr(sqlite_store, "_BUSY_TIMEOUT_MS", 50)
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    ctx = _ctx()
    await _interrupt(store, ctx)

    peer = sqlite3.connect(tmp_path / "events.sqlite3")
    peer.execute("BEGIN IMMEDIATE")
    peer.execute("INSERT INTO events (tenant, log_key, run_id, seq, data) VALUES ('acme', 's-1', 'r-9', 0, '{}')")
    try:
        with pytest.raises(StoreError) as raised:
            await store.claim_resume("s-1", "r-1", _resumed(2), ctx)
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert "locked" in str(raised.value.__cause__)
    finally:
        peer.rollback()
        peer.close()
        store.close()
