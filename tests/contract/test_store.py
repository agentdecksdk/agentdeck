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
from agentdeck.core.events import Event, RunCompleted, RunInterrupted, RunStarted, TextDelta
from agentdeck.core.status import RunStatus

if TYPE_CHECKING:
    from agentdeck.core.ports import EventStorePort

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(params=[MemoryEventStore, SqliteEventStore], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest) -> EventStorePort:
    return request.param()


def _ctx(tenant: str = "acme") -> RunContext:
    return RunContext(tenant=tenant, principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


def _event(seq: int, payload: object, tenant: str = "acme", run_id: str = "r-1", log_key: str = "s-1") -> Event:
    return Event(
        kind=payload.kind,  # type: ignore[attr-defined]
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


async def test_last_seq_is_negative_one_for_a_run_with_no_events(store: EventStorePort) -> None:
    assert await store.last_seq("s-1", "r-1", _ctx()) == -1


async def test_last_seq_tracks_the_highest_seq_appended_for_that_run(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append("s-1", [_event(0, _started()), _event(1, TextDelta(message_id="m1", text="hi"))], ctx)
    assert await store.last_seq("s-1", "r-1", ctx) == 1


async def test_last_seq_is_scoped_to_one_run_not_the_whole_log(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await store.append("s-1", [_event(0, _started(), run_id="r-2"), _event(1, _started(), run_id="r-2")], ctx)
    assert await store.last_seq("s-1", "r-1", ctx) == 0
    assert await store.last_seq("s-1", "r-2", ctx) == 1
    assert await store.last_seq("s-1", "r-3", ctx) == -1


async def test_run_status_with_no_events_is_pending(store: EventStorePort) -> None:
    assert await store.run_status("s-1", "r-1", _ctx()) is RunStatus.PENDING


async def test_run_status_follows_the_last_lifecycle_transition(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append(
        "s-1",
        [
            _event(0, _started()),
            _event(1, RunInterrupted(interrupt_id="i-1", reason="human", payload={"q": "ok?"}, thread_id="t-1")),
        ],
        ctx,
    )
    assert await store.run_status("s-1", "r-1", ctx) is RunStatus.WAITING_HUMAN


async def test_run_status_is_scoped_to_one_run_not_the_whole_log(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await store.append(
        "s-1",
        [
            _event(0, _started(), run_id="r-2"),
            _event(1, RunCompleted(output=[], usage={"input_tokens": 1, "output_tokens": 1}), run_id="r-2"),
        ],
        ctx,
    )
    assert await store.run_status("s-1", "r-1", ctx) is RunStatus.RUNNING
    assert await store.run_status("s-1", "r-2", ctx) is RunStatus.COMPLETED


async def test_list_runs_scopes_to_one_tenant(store: EventStorePort) -> None:
    await store.append("s-1", [_event(0, _started(), tenant="acme")], _ctx("acme"))
    await store.append("s-1", [_event(0, _started(), tenant="globex")], _ctx("globex"))

    acme_runs = await store.list_runs(_ctx("acme"))
    assert [summary.run_id for summary in acme_runs] == ["r-1"]


async def test_list_runs_filters_by_status(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append("s-1", [_event(0, _started(), run_id="r-1")], ctx)
    await store.append(
        "s-1",
        [
            _event(0, _started(), run_id="r-2"),
            _event(1, RunInterrupted(interrupt_id="i-1", reason="human", payload={}, thread_id="t-2"), run_id="r-2"),
        ],
        ctx,
    )

    waiting = await store.list_runs(ctx, status=RunStatus.WAITING_HUMAN)
    assert [(summary.run_id, summary.status) for summary in waiting] == [("r-2", RunStatus.WAITING_HUMAN)]

    everyone = await store.list_runs(ctx)
    assert {summary.run_id for summary in everyone} == {"r-1", "r-2"}


async def test_paginated_read_after_skips_the_first_n_events(store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await store.append("s-1", events, ctx)
    page = await store.read("s-1", ctx, after=2)
    assert [event.seq for event in page] == [2, 3, 4]


async def test_paginated_read_limit_caps_the_page(store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await store.append("s-1", events, ctx)
    page = await store.read("s-1", ctx, limit=2)
    assert [event.seq for event in page] == [0, 1]


async def test_paginated_read_after_and_limit_compose_into_the_next_page(store: EventStorePort) -> None:
    ctx = _ctx()
    events = [_event(seq, TextDelta(message_id="m1", text=str(seq))) for seq in range(5)]
    await store.append("s-1", events, ctx)
    page = await store.read("s-1", ctx, after=2, limit=2)
    assert [event.seq for event in page] == [2, 3]


async def test_paginated_read_past_the_end_is_empty(store: EventStorePort) -> None:
    ctx = _ctx()
    await store.append("s-1", [_event(0, _started())], ctx)
    assert await store.read("s-1", ctx, after=10) == []
