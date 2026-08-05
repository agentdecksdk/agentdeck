"""Store contract: the focused queries — ``last_seq``, ``run_status``, ``list_runs`` and
paginated ``read`` — behave identically on every store, parametrized the same way the
engine cases are. Ordering/tenancy/round-trip invariants for ``append``, ``read`` and
``read_run`` already live in ``tests/test_memory_store.py`` and ``tests/test_sqlite_store.py``;
this file covers only the newer focused ops.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
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
from agentdeck.core.status import RunStatus

if TYPE_CHECKING:
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
