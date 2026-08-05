"""Store contract: the focused queries — ``last_seq``, ``run_status``, ``list_runs`` and
paginated ``read`` — behave identically on every store, parametrized the same way the
engine cases are. Ordering/tenancy/round-trip invariants for ``append``, ``read`` and
``read_run`` already live in ``tests/test_memory_store.py`` and ``tests/test_sqlite_store.py``;
this file covers only the newer focused ops.

The last case is a boundary invariant rather than a query one, and covers both SQLite-backed
ports by shape: whatever fails underneath, callers see the harness's own error type.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.adapters.stores.sqlite import store as sqlite_store
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    Event,
    KnownPayload,
    RunCompleted,
    RunInterrupted,
    RunResumed,
    RunStarted,
    TextDelta,
)
from agentdeck.core.ports.control import Signal
from agentdeck.core.status import RunStatus
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

    from agentdeck.core.ports import EventStorePort

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(params=[MemoryEventStore, SqliteEventStore], ids=["memory", "sqlite"])
def event_store(request: pytest.FixtureRequest) -> EventStorePort:
    return request.param()


def _ctx(tenant: str = "acme") -> RunContext:
    return RunContext(tenant=tenant, principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


def _event(seq: int, payload: KnownPayload, tenant: str = "acme", run_id: str = "r-1", log_key: str = "s-1") -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id=log_key,
        tenant=tenant,
        origin="Greeter",
        ts=TS,
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


@pytest.mark.parametrize("kind", ["pending", "running", "completed"])
async def test_claim_resume_refuses_a_run_that_is_not_waiting_on_a_human(
    event_store: EventStorePort, kind: str
) -> None:
    """A resume against any other status is a no-op, not an error — including a run this
    store has never heard of, which is indistinguishable from one that never started."""
    ctx = _ctx()
    if kind == "running":
        await event_store.append("s-1", [_event(0, _started())], ctx)
    elif kind == "completed":
        await event_store.append(
            "s-1",
            [_event(0, _started()), _event(1, RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1}))],
            ctx,
        )

    assert await event_store.claim_resume("s-1", "r-1", _resumed(9), ctx) is False
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
