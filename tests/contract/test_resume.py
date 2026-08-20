"""Resume invariants (#53): every case that ends ``"suspended"`` gets resumed here, on
every engine that has one  -  stub and langgraph today, the shared invariants a new
suspending engine inherits automatically by adding its case to ``contract_cases.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from event_log_checks import check_contiguous, check_terminal

from agentdeck.core.events import RunInterrupted

if TYPE_CHECKING:
    from case_types import Case

    from agentdeck.adapters.stores.memory import MemoryEventStore
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.runtime.service import Runtime

RESUME_VALUE = "approved"


@pytest.fixture
def suspended_case(case: Case) -> Case:
    if case.ends != "suspended":
        pytest.skip("only a suspended case can be resumed")
    return case


async def _interrupt_thread_id(events: list[Event]) -> str:
    payload = events[-1].payload
    assert isinstance(payload, RunInterrupted)
    assert payload.thread_id is not None
    return payload.thread_id


async def test_resume_continues_seq_with_exactly_one_terminal_event_at_the_end(
    suspended_case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    opening = [
        event
        async for event in runtime.run(
            suspended_case.spec.name, suspended_case.input, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    run_id = opening[0].run_id
    # The executor reads this off the log now, so what a caller owes is only that the
    # interrupt carried one at all.
    assert await _interrupt_thread_id(opening)

    resumed = [
        event
        async for event in runtime.resume(
            suspended_case.spec.name,
            RESUME_VALUE,
            run_id=run_id,
            session_id=ctx.session_id,
            namespace=ctx.namespace,
        )
    ]
    assert resumed  # a claim that wins always yields at least run.resumed

    whole = opening + resumed
    assert check_terminal(whole) is None
    assert check_contiguous(whole) == []
    assert [event.seq for event in whole] == list(range(len(whole)))
    assert await store.read(ctx.log_key, ctx) == whole


async def test_a_stray_resume_on_an_already_completed_run_is_a_noop(
    suspended_case: Case, runtime: Runtime, ctx: RunContext
) -> None:
    opening = [
        event
        async for event in runtime.run(
            suspended_case.spec.name, suspended_case.input, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    run_id = opening[0].run_id
    # The executor reads this off the log now, so what a caller owes is only that the
    # interrupt carried one at all.
    assert await _interrupt_thread_id(opening)

    first = [
        event
        async for event in runtime.resume(
            suspended_case.spec.name,
            RESUME_VALUE,
            run_id=run_id,
            session_id=ctx.session_id,
            namespace=ctx.namespace,
        )
    ]
    assert first

    second = [
        event
        async for event in runtime.resume(
            suspended_case.spec.name,
            RESUME_VALUE,
            run_id=run_id,
            session_id=ctx.session_id,
            namespace=ctx.namespace,
        )
    ]
    assert second == []


async def test_two_concurrent_resumes_have_exactly_one_winner_and_no_duplicate_seq(
    suspended_case: Case, runtime: Runtime, store: MemoryEventStore, ctx: RunContext
) -> None:
    opening = [
        event
        async for event in runtime.run(
            suspended_case.spec.name, suspended_case.input, session_id=ctx.session_id, namespace=ctx.namespace
        )
    ]
    run_id = opening[0].run_id
    # The executor reads this off the log now, so what a caller owes is only that the
    # interrupt carried one at all.
    assert await _interrupt_thread_id(opening)

    async def _collect() -> list[Event]:
        return [
            event
            async for event in runtime.resume(
                suspended_case.spec.name,
                RESUME_VALUE,
                run_id=run_id,
                session_id=ctx.session_id,
                namespace=ctx.namespace,
            )
        ]

    first, second = await asyncio.gather(_collect(), _collect())

    assert sorted([bool(first), bool(second)]) == [False, True]  # exactly one winner

    stored = await store.read(ctx.log_key, ctx)
    seqs = [event.seq for event in stored]
    assert len(seqs) == len(set(seqs))
    assert check_contiguous(stored) == []
