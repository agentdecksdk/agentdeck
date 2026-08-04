"""The invariants every engine must satisfy, run against every engine.

These are the promises consumers are allowed to build on. A new invariant discovered
anywhere belongs here, not as a one-off next to whatever found it.
"""

from __future__ import annotations

from contextlib import aclosing
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from agentdeck.core.events import TERMINAL_KINDS, check_contiguous, check_terminal
from agentdeck.runtime.service import SUSPENDED_KINDS

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from contract_cases import Case, Played

    from agentdeck.adapters.stores.memory import MemoryEventStore
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.runtime.service import Runtime


def test_a_run_opens_with_run_started_at_seq_zero(played: Played) -> None:
    """The join point: one ``run.started``, first, carrying the run's constants."""
    kinds = [event.kind for event in played.events]
    assert kinds[0] == "run.started"
    assert kinds.count("run.started") == 1
    assert played.events[0].seq == 0


def test_seq_is_contiguous_from_zero(played: Played) -> None:
    """No gaps — which is what lets a consumer detect a dropped event instead of guessing."""
    assert check_contiguous(played.events) == []
    assert [event.seq for event in played.events] == list(range(len(played.events)))


def test_a_finished_run_has_exactly_one_terminal_event_and_it_is_last(case: Case, played: Played) -> None:
    """Holds even when the engine misbehaved: the Runtime closes a run its engine left open."""
    if case.ends == "suspended":
        pytest.skip("a suspended run's terminal event arrives on resume")
    assert check_terminal(played.events) is None


def test_a_suspended_run_ends_waiting_and_emits_no_terminal_event(case: Case, played: Played) -> None:
    if case.ends != "suspended":
        pytest.skip("this run finished")
    assert played.events[-1].kind in SUSPENDED_KINDS
    assert [event.kind for event in played.events if event.kind in TERMINAL_KINDS] == []


def test_the_envelope_comes_from_the_context_not_the_engine(case: Case, played: Played, ctx: RunContext) -> None:
    """An engine cannot set tenancy, identity or attribution — it never sees the envelope."""
    for event in played.events:
        assert (event.tenant, event.run_id, event.session_id) == (ctx.tenant, ctx.run_id, ctx.session_id)
        assert event.origin == case.spec.name
        assert event.v == 1
        assert event.kind == event.payload.kind


async def test_every_event_is_in_the_store_before_a_consumer_sees_it(
    case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    """Persist-before-yield, checked at every step rather than at the end: a consumer that
    spots a gap can always refetch it, because the store is never behind the stream."""
    seen = 0
    async for event in _tolerant(runtime.run(case.spec.name, case.input, ctx)):
        seen += 1
        stored = await store.read(ctx.log_key, ctx)
        assert stored[-1] == event
        assert len(stored) == seen


async def test_the_stream_and_the_store_tell_the_same_story(
    played: Played, store: MemoryEventStore, ctx: RunContext
) -> None:
    assert await store.read(ctx.log_key, ctx) == played.events


async def test_an_abandoned_stream_leaves_a_closed_run_behind(
    case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    """A consumer that walks away mid-run — closed tab, killed CLI — truncates the log at an
    event boundary, and the run is closed there: a later reader must be able to tell
    "abandoned" from "still in flight", which is the same reason an open run is a bug."""
    async with aclosing(runtime.run(case.spec.name, case.input, ctx)) as run:
        async for _ in run:
            break
    stored = await store.read(ctx.log_key, ctx)
    assert [event.seq for event in stored] == list(range(len(stored)))
    assert stored[0].kind == "run.started"
    assert check_terminal(stored) is None
    assert stored[-1].kind == "run.cancelled"


async def test_a_gap_can_be_refetched_from_the_store_by_run(
    case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    """What contiguous ``seq`` buys: a consumer that missed events asks the store for that run
    from the gap onward — and gets that run's tail only, even with two runs in the log."""
    first = [event async for event in _tolerant(runtime.run(case.spec.name, case.input, ctx))]
    later_ctx = replace(ctx, run_id="r-2")
    second = [event async for event in _tolerant(runtime.run(case.spec.name, case.input, later_ctx))]

    assert await store.read_run(ctx.log_key, "r-1", ctx) == first
    assert await store.read_run(ctx.log_key, "r-2", later_ctx) == second
    assert await store.read_run(ctx.log_key, "r-2", later_ctx, from_seq=1) == second[1:]


async def test_a_second_run_in_the_session_counts_its_seq_from_zero_again(
    case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    """``seq`` is per run, not per session: two runs in one log each count from 0, and the log
    keeps both stories end to end."""
    first = [event async for event in _tolerant(runtime.run(case.spec.name, case.input, ctx))]
    second = [event async for event in _tolerant(runtime.run(case.spec.name, case.input, replace(ctx, run_id="r-2")))]

    assert [event.seq for event in second] == [event.seq for event in first]
    assert await store.read(ctx.log_key, ctx) == first + second


async def _tolerant(events: AsyncIterator[Event]) -> AsyncGenerator[Event, None]:
    """Iterate a run, swallowing the engine's exception — these tests assert on the log, and a
    scripted failure is one of the cases."""
    try:
        async for event in events:
            yield event
    except Exception:
        return
