"""Runtime behavior the contract suite can't state as an engine invariant: lookup failures,
sink fan-out, and where a sessionless run's events land.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from never_yields import NeverYields
from pydantic import ValidationError

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    Event,
    RunCompleted,
    RunContextSnapshot,
    RunFailed,
    RunInterrupted,
    RunStarted,
    TextDelta,
    Usage,
    UsageReported,
    check_contiguous,
    check_terminal,
)
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import EventSinkPort, SessionClaim
from agentdeck.errors import ConfigError, NotFoundError, SessionBusyError, StoreError
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import RuntimeSettings, reset_settings_cache

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.events import KnownPayload

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
INPUT = [TextBlock(text="hi")]
DONE = RunCompleted(output=[TextBlock(text="hi back")], usage=Usage(input_tokens=1, output_tokens=2))


CTX = RunContext(tenant="acme", principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")

# A wedge detector, not a budget: everything here is in-process and takes microseconds.
WEDGE_TIMEOUT = 5.0


def _runtime(*, sinks: list[EventSinkPort] | None = None) -> tuple[Runtime, MemoryEventStore]:
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    return Runtime([StubEngine()], store, {spec.name: spec}, sinks=sinks or [], clock=lambda: TS), store


class Recorder(EventSinkPort):
    """A sink that awaits before recording, so it can only pass if the port's promise holds:
    every event arrives, one at a time, in the order it was submitted."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        await asyncio.sleep(0)
        self.events.append(event)

    def by_seq(self) -> list[Event]:
        return sorted(self.events, key=lambda event: event.seq)


class Broken(EventSinkPort):
    """A sink that always fails — the run must not care."""

    async def emit(self, event: Event) -> None:
        raise RuntimeError("sink is down")


async def test_an_unknown_invocable_is_reported_before_anything_is_written() -> None:
    runtime, store = _runtime()
    with pytest.raises(NotFoundError, match="Nope"):
        async for _ in runtime.run("Nope", INPUT, CTX):
            pass
    assert await store.read(CTX.log_key, CTX) == []


async def test_an_invocable_whose_engine_is_not_registered_is_reported() -> None:
    spec = InvocableSpec(name="Ghost", kind=InvocableKind.AGENT, engine="temporal")
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec})
    with pytest.raises(NotFoundError, match="temporal"):
        async for _ in runtime.run("Ghost", INPUT, CTX):
            pass


async def test_the_envelope_timestamp_comes_from_the_injected_clock() -> None:
    runtime, _ = _runtime()
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    assert {event.ts for event in events} == {TS}


async def test_run_started_carries_the_context_snapshot() -> None:
    runtime, _ = _runtime()
    ctx = replace(CTX, principal="user:9", trace_id="tr-9", triggered_by="cron", parent_run_id="r-0")
    opening = [event async for event in runtime.run("Greeter", INPUT, ctx)][0]
    assert opening.payload.context.principal == "user:9"
    assert opening.payload.context.trace_id == "tr-9"
    assert opening.payload.context.triggered_by == "cron"
    assert opening.payload.parent_run_id == "r-0"
    assert opening.payload.input == INPUT


async def test_a_run_without_a_session_is_still_persisted_under_its_own_id() -> None:
    """Otherwise persist-before-yield would quietly not apply to one-off runs."""
    runtime, store = _runtime()
    ctx = replace(CTX, session_id=None)
    events = [event async for event in runtime.run("Greeter", INPUT, ctx)]
    assert await store.read("r-1", ctx) == events
    assert all(event.session_id is None for event in events)


async def test_sinks_see_every_event_without_the_run_waiting_for_them() -> None:
    recorder = Recorder()
    runtime, _ = _runtime(sinks=[recorder])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    # the run finished without awaiting the sink; the sink catches up right after
    await runtime.drain()
    assert recorder.by_seq() == events


async def test_sinks_see_the_resume_events_too_not_just_the_opening_run() -> None:
    """``run.resumed`` is written by the store's conditional append, not the ordinary record
    path, so its fan-out is its own line — and a sink silently missing every resume (audit,
    cost, observability) is exactly what nothing else in the suite would notice.
    """
    recorder = Recorder()
    spec = stub_spec(
        "Approver",
        RunInterrupted(interrupt_id="i1", reason="approval", payload={}, thread_id="t1"),
        DONE,
        kind=InvocableKind.WORKFLOW,
    )
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, sinks=[recorder], clock=lambda: TS)

    opening = [event async for event in runtime.run("Approver", INPUT, CTX)]
    resumed = [event async for event in runtime.resume("Approver", "t1", "approved", CTX)]
    await runtime.drain()

    assert [event.kind for event in resumed] == ["run.resumed", "run.completed"]
    assert recorder.by_seq() == opening + resumed


async def test_a_failing_sink_does_not_fail_the_run_or_starve_the_others() -> None:
    recorder = Recorder()
    runtime, store = _runtime(sinks=[Broken(), recorder])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    await runtime.drain()
    assert [event.kind for event in events][-1] == "run.completed"
    assert await store.read(CTX.log_key, CTX) == events
    assert recorder.by_seq() == events


async def test_drain_waits_for_the_sink_emits_still_in_flight() -> None:
    """Without it, pending emits die with the event loop and the last audit events vanish."""
    recorder = Recorder()
    runtime, _ = _runtime(sinks=[recorder])
    async for _ in runtime.run("Greeter", INPUT, CTX):
        pass
    await runtime.drain()
    assert len(recorder.events) == 3  # that the run never waited for them is the slow-sink test


async def test_a_sink_receives_the_stream_in_order_and_one_event_at_a_time() -> None:
    """Each sink is fed from its own queue by a single consumer, so nothing re-enters ``emit``."""

    class Reentrant(EventSinkPort):
        def __init__(self) -> None:
            self.events: list[Event] = []
            self.inside = False
            self.overlapped = False

        async def emit(self, event: Event) -> None:
            self.overlapped = self.overlapped or self.inside
            self.inside = True
            await asyncio.sleep(0)
            self.events.append(event)
            self.inside = False

    sink = Reentrant()
    runtime, _ = _runtime(sinks=[sink])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    await runtime.drain()
    assert sink.events == events
    assert sink.overlapped is False


async def test_a_slow_sink_does_not_hold_up_the_stream() -> None:
    """NFR-6: the run must not be pinned to its slowest reader."""

    class Slow(EventSinkPort):
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def emit(self, event: Event) -> None:
            await self.release.wait()

    slow = Slow()
    runtime, _ = _runtime(sinks=[slow])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    assert [event.kind for event in events][-1] == "run.completed"
    slow.release.set()
    await runtime.drain()


async def test_a_healthy_sink_sees_a_long_run_whole_even_when_the_store_never_yields_either() -> None:
    """Liveness here is the dispatch's own job, not something borrowed from the store (issue
    #87): wrapped in ``NeverYields``, not even the store's ``append`` hands the loop a turn,
    so a whole run can still outrun the queue without the loop turning once except when the
    dispatch decides to. A sink that is keeping up must not be charged for that — it is the
    producer that is fast, not the sink that is slow — and this is the profile that proves it
    without quietly relying on the store's own scheduling to cover for a dispatch regression."""
    spec = stub_spec("Firehose", *[TextDelta(message_id="m1", text=str(n)) for n in range(1000)], DONE)
    recorder = Recorder()
    store = NeverYields(MemoryEventStore())
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, sinks=[recorder], clock=lambda: TS)

    events = [event async for event in runtime.run("Firehose", INPUT, CTX)]
    await runtime.drain()

    assert len(events) == 1002  # more than four queues' worth
    assert recorder.events == events


async def test_a_stalling_sink_costs_one_task_however_many_events_it_misses() -> None:
    """A fan-out that spawned a task per event grew one pending task per event past a wedged
    sink; a queue per sink is what makes the cost of a bad sink fixed instead."""

    class Stalled(EventSinkPort):
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def emit(self, event: Event) -> None:
            await self.release.wait()

    spec = stub_spec("Chatty", *[TextDelta(message_id="m1", text=str(n)) for n in range(60)], DONE)
    stalled = Stalled()
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, sinks=[stalled], clock=lambda: TS)

    before = len(asyncio.all_tasks())
    events = [event async for event in runtime.run("Chatty", INPUT, CTX)]

    assert len(events) == 62
    assert [event.kind for event in events][-1] == "run.completed"
    assert len(asyncio.all_tasks()) == before + 1  # the sink's one consumer, not one task per event

    stalled.release.set()
    await runtime.drain()
    assert len(asyncio.all_tasks()) == before


async def test_the_engine_exception_reaches_the_caller_after_run_failed_is_recorded() -> None:
    """Both, always: the event is the record, the exception is the caller's."""
    spec = stub_spec("Boom", TextDelta(message_id="m1", text="almost"), ValueError("secret detail"))
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    seen: list[Event] = []
    with pytest.raises(ValueError, match="secret detail"):
        async for event in runtime.run("Boom", INPUT, CTX):
            seen.append(event)

    assert [event.kind for event in seen] == ["run.started", "text.delta", "run.failed"]
    assert seen[-1].payload.error_code == "engine_error"
    assert seen[-1].payload.retryable is False
    assert await store.read(CTX.log_key, CTX) == seen


async def test_run_failed_names_the_exception_type_and_not_its_message() -> None:
    """An exception message can carry request content or a secret; sinks must not receive it."""
    spec = stub_spec("Leaky", ValueError("sk-live-abc123"))
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, clock=lambda: TS)

    seen: list[Event] = []
    with pytest.raises(ValueError):
        async for event in runtime.run("Leaky", INPUT, CTX):
            seen.append(event)

    assert "sk-live-abc123" not in seen[-1].payload.message
    assert "ValueError" in seen[-1].payload.message


async def test_an_engine_that_stops_without_a_terminal_event_gets_one_anyway() -> None:
    """A silent engine would leave every consumer waiting forever."""
    spec = stub_spec("Quitter", TextDelta(message_id="m1", text="and then nothing"))
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, clock=lambda: TS)
    events = [event async for event in runtime.run("Quitter", INPUT, CTX)]
    assert [event.kind for event in events] == ["run.started", "text.delta", "run.failed"]
    assert events[-1].payload.error_code == "engine_error"


async def test_the_engine_receives_the_history_the_store_holds() -> None:
    """Second turn of a session: the engine is handed what already happened."""
    seen_history: list[list[Event]] = []

    class Nosy(StubEngine):
        async def start(
            self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
        ) -> AsyncGenerator[KnownPayload, None]:
            seen_history.append(list(history))
            yield DONE

    spec = stub_spec("Greeter")
    store = MemoryEventStore()
    runtime = Runtime([Nosy()], store, {spec.name: spec}, clock=lambda: TS)

    first = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    async for _ in runtime.run("Greeter", INPUT, replace(CTX, run_id="r-2")):
        pass

    assert seen_history[0] == []
    assert seen_history[1] == first


async def test_nothing_an_engine_yields_after_a_terminal_payload_reaches_the_log() -> None:
    """Recording it would produce a log that says the run both completed and failed — strictly
    worse than the open run the no-terminal guard exists for."""
    spec = stub_spec(
        "Chatterbox",
        DONE,
        UsageReported(model="fake", usage=Usage(input_tokens=1, output_tokens=1)),
        RunFailed(error_code="tool_error", message="too late", retryable=True),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    events = [event async for event in runtime.run("Chatterbox", INPUT, CTX)]
    assert [event.kind for event in events] == ["run.started", "run.completed"]
    assert check_terminal(events) is None
    assert await store.read(CTX.log_key, CTX) == events


async def test_an_abandoned_run_is_closed_in_the_log() -> None:
    """A consumer that walks away leaves a run that will never produce another event; without a
    terminal one, a later reader cannot tell it from a run still in flight."""
    spec = stub_spec("Chatty", *[TextDelta(message_id="m1", text=str(n)) for n in range(3)], DONE)
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    async with aclosing(runtime.run("Chatty", INPUT, CTX)) as run:
        async for _ in run:
            break

    stored = await store.read(CTX.log_key, CTX)
    assert [event.kind for event in stored] == ["run.started", "run.cancelled"]
    assert check_terminal(stored) is None
    assert stored[-1].payload.reason == "consumer stopped reading"


async def test_a_completed_run_is_not_cancelled_when_its_consumer_lets_go() -> None:
    """The close path must not fire on a run that already ended — that would be two terminals."""
    runtime, store = _runtime()
    async with aclosing(runtime.run("Greeter", INPUT, CTX)) as run:
        async for _ in run:
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)][-1] == "run.completed"


async def test_a_suspended_run_is_not_cancelled_when_its_consumer_lets_go() -> None:
    """An interrupted run is legitimately waiting; closing the stream must not close the run."""
    spec = stub_spec("Approver", RunInterrupted(interrupt_id="i1", reason="approval", payload={}))
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    async with aclosing(runtime.run("Approver", INPUT, CTX)) as run:
        async for _ in run:
            pass

    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started", "run.interrupted"]


async def test_an_invocable_with_no_script_is_a_config_error() -> None:
    """A misconfigured invocable is the caller's mistake, not a run that failed."""
    spec = InvocableSpec(name="Empty", kind=InvocableKind.AGENT, engine="stub", native=None)
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, clock=lambda: TS)

    with pytest.raises(ConfigError, match="no stub script"):
        async for _ in runtime.run("Empty", INPUT, CTX):
            pass


class _Blocking(StubEngine):
    """Holds a run open at its first event until released, so a second turn really does arrive
    while the first one is in flight rather than after it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(
        self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        self.entered.set()
        await self.release.wait()
        async for payload in super().start(spec, input, history, ctx):
            yield payload


class _Stalling(StubEngine):
    """Plays one named run's first payload and then waits to be released: a turn that has gone
    quiet mid-run, which is the state a staleness window cannot tell from a process that died.
    Every other run plays straight through, so the turn that takes the session over is not
    stalled by the same fixture."""

    def __init__(self, quiet_run: str) -> None:
        self._quiet_run = quiet_run
        self.quiet = asyncio.Event()
        self.release = asyncio.Event()

    async def start(
        self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        stream = super().start(spec, input, history, ctx)
        if ctx.run_id == self._quiet_run:
            yield await anext(stream)
            self.quiet.set()
            await self.release.wait()
        async for payload in stream:
            yield payload


class _Ticking:
    """A clock that moves a second every time it is read, so a run's own events are already old
    when the next turn asks about them — staleness without a test waiting for a wall clock."""

    def __init__(self) -> None:
        self._at = TS

    def __call__(self) -> datetime:
        self._at += timedelta(seconds=1)
        return self._at


def _abandoned(run_id: str, ts: datetime, origin: str = "Ghost") -> Event:
    """A run left open by a process that died: nothing else produces one, because a Runtime that
    exits at all closes its own run in the log."""
    context = RunContextSnapshot(principal=CTX.principal, trace_id=CTX.trace_id)
    opening = RunStarted(invocable=origin, kind_of_invocable="agent", input=INPUT, context=context)
    return Event(
        kind=opening.kind,
        seq=0,
        run_id=run_id,
        session_id=CTX.session_id,
        tenant=CTX.tenant,
        origin=origin,
        ts=ts,
        payload=opening,
    )


async def test_a_turn_arriving_while_another_is_in_flight_is_refused() -> None:
    """One session, one turn. The refusal names the session and the run holding it, and the turn
    that already had it runs on untouched."""
    engine = _Blocking()
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([engine], store, {spec.name: spec}, clock=lambda: TS)

    async def _play(run_id: str) -> list[Event]:
        return [event async for event in runtime.run("Greeter", INPUT, replace(CTX, run_id=run_id))]

    first = asyncio.create_task(_play("r-1"))
    try:
        # Bounded, because the failure mode of a claim that does not refuse is this test waiting
        # on the engine it blocked: a wedge has to fail rather than hang the suite.
        async with asyncio.timeout(WEDGE_TIMEOUT):
            await engine.entered.wait()
            with pytest.raises(SessionBusyError) as refused:
                await _play("r-2")
    finally:
        engine.release.set()

    assert "'s-1'" in str(refused.value)
    assert "'r-1'" in str(refused.value)
    assert [event.kind for event in await first][-1] == "run.completed"
    assert {event.run_id for event in await store.read(CTX.log_key, CTX)} == {"r-1"}


async def test_a_turn_on_a_session_whose_run_is_waiting_on_a_human_is_refused() -> None:
    """A suspended run has not finished: it still owns the engine thread it will resume on, so a
    new turn there would run over the state that resume needs."""
    spec = stub_spec("Approver", RunInterrupted(interrupt_id="i1", reason="approval", payload={}))
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    assert [event async for event in runtime.run("Approver", INPUT, CTX)][-1].kind == "run.interrupted"
    with pytest.raises(SessionBusyError, match="r-1"):
        async for _ in runtime.run("Approver", INPUT, replace(CTX, run_id="r-2")):
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started", "run.interrupted"]


async def test_a_turn_after_the_previous_one_finished_is_not_refused() -> None:
    """The ordinary case the claim must leave alone: a conversation is a sequence of turns."""
    runtime, store = _runtime()
    first = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    second = [event async for event in runtime.run("Greeter", INPUT, replace(CTX, run_id="r-2"))]

    assert first[-1].kind == "run.completed"
    assert second[-1].kind == "run.completed"
    assert await store.read(CTX.log_key, CTX) == first + second


async def test_two_runs_without_a_session_never_contend() -> None:
    """A sessionless run is its own log, so there is nobody in it to be busy: two at once share
    no conversation and must both play."""
    engine = _Blocking()
    engine.release.set()
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    runtime = Runtime([engine], MemoryEventStore(), {spec.name: spec}, clock=lambda: TS)

    async def _play(run_id: str) -> list[Event]:
        ctx = replace(CTX, run_id=run_id, session_id=None)
        return [event async for event in runtime.run("Greeter", INPUT, ctx)]

    both = await asyncio.gather(_play("r-1"), _play("r-2"))
    assert [events[-1].kind for events in both] == ["run.completed", "run.completed"]


async def test_a_turn_takes_over_a_session_whose_run_went_silent_and_closes_it_as_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hard-kill case: the run holding this session stopped writing long enough ago that
    nothing is coming back for it. The new turn proceeds, the abandoned run is closed under its
    own name and ``seq``, and the takeover is on the record — it may always be premature."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    await store.append(CTX.log_key, [_abandoned("r-0", TS - timedelta(minutes=10))], CTX)
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS, stale_run_after=timedelta(minutes=5))

    with caplog.at_level(logging.WARNING):
        events = [event async for event in runtime.run("Greeter", INPUT, CTX)]

    assert events[-1].kind == "run.completed"
    closed = await store.read_run(CTX.log_key, "r-0", CTX)
    assert [event.kind for event in closed] == ["run.started", "run.failed"]
    assert closed[-1].seq == 1
    assert closed[-1].origin == "Ghost", "the closing event belongs to the abandoned run, not the turn that closed it"
    assert closed[-1].payload.error_code == "cancelled_hard"
    assert "r-1" in closed[-1].payload.message
    assert "took it over and closed it as failed" in caplog.text


async def test_a_turn_does_not_take_over_a_session_whose_run_is_merely_quiet() -> None:
    """The other side of the window, and the reason it is generous: a run silent for less than it
    keeps its session, so a double-clicked send cannot steal a turn that is still working."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    await store.append(CTX.log_key, [_abandoned("r-0", TS - timedelta(minutes=1))], CTX)
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS, stale_run_after=timedelta(minutes=5))

    with pytest.raises(SessionBusyError, match="r-0"):
        async for _ in runtime.run("Greeter", INPUT, CTX):
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started"]


async def test_a_run_that_writes_again_after_being_taken_over_fails_instead_of_reusing_a_seq() -> None:
    """The cost of a takeover that was premature, bounded. The run it stepped over was alive after
    all and writes its next event at a ``seq`` the closing event already used: one ``seq`` per run
    is what a consumer refetches a gap with, so the store refuses that write and the run fails
    loudly. It does end twice in the record — its own failure lands after the takeover's — which is
    detectable, unlike two different events answering to one ``seq``.
    """
    engine = _Stalling("r-1")
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([engine], store, {spec.name: spec}, clock=_Ticking(), stale_run_after=timedelta(seconds=1))

    quiet = asyncio.create_task(_collect(runtime, "r-1"))
    async with asyncio.timeout(WEDGE_TIMEOUT):
        await engine.quiet.wait()
        # The quiet run's last event is older than the window by the ticking clock alone.
        taken = [event async for event in runtime.run("Greeter", INPUT, replace(CTX, run_id="r-2"))]
        engine.release.set()
        with pytest.raises(StoreError, match="already in log"):
            await quiet

    assert taken[-1].kind == "run.completed"
    resurrected = await store.read_run(CTX.log_key, "r-1", CTX)
    seqs = [event.seq for event in resurrected]
    assert seqs == sorted(set(seqs)), f"a seq was written twice: {[(e.seq, e.kind) for e in resurrected]}"
    assert check_contiguous(resurrected) == []
    assert [event.kind for event in resurrected] == ["run.started", "text.delta", "run.failed", "run.failed"]
    # A refused write is the log's doing, and the record must not put it on the engine.
    assert resurrected[-1].payload.message == "StoreError recording this run"


async def _collect(runtime: Runtime, run_id: str) -> list[Event]:
    return [event async for event in runtime.run("Greeter", INPUT, replace(CTX, run_id=run_id))]


async def test_a_cancellation_during_the_claim_closes_the_run_and_frees_the_session() -> None:
    """The gap between committing a run and having anything to yield. The claim is awaited in the
    caller's own coroutine — the one an ASGI server cancels when a client disconnects before the
    response starts — and a cancellation there used to leave the run open, holding its session for
    a whole staleness window with no terminal event to explain why.
    """

    class _SlowClaim(MemoryEventStore):
        """Commits the claim and then hangs, so a cancellation can land in exactly that gap."""

        def __init__(self) -> None:
            super().__init__()
            self.committed = asyncio.Event()

        async def claim_start(
            self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime
        ) -> SessionClaim:
            claim = await super().claim_start(log_key, event, ctx, stale_before)
            if event.run_id == "r-1":
                self.committed.set()
                await asyncio.Event().wait()  # a cancellation is the only way out, which is the point
            return claim

    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = _SlowClaim()
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

    turn = asyncio.create_task(_collect(runtime, "r-1"))
    async with asyncio.timeout(WEDGE_TIMEOUT):
        await store.committed.wait()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        stored = await store.read(CTX.log_key, CTX)
        assert [event.kind for event in stored] == ["run.started", "run.cancelled"]
        assert check_terminal(stored) is None
        assert stored[-1].payload.reason == "cancelled during the claim"

        # And the session is free again — the point of closing it rather than waiting the window out.
        next_turn = [event async for event in runtime.run("Greeter", INPUT, replace(CTX, run_id="r-2"))]
        assert next_turn[-1].kind == "run.completed"


async def test_a_takeover_whose_bookkeeping_fails_still_leaves_this_turn_runnable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The claim is committed before the abandoned run is closed, so a store failure in between
    must not escape: this run would be left open with no terminal event, holding the session it
    just took for a whole window. The close is dropped, reported, and left to the next turn."""

    class _CannotClose(MemoryEventStore):
        async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
            if run_id == "r-0":
                raise StoreError("the log went away mid-takeover")
            return await super().last_seq(log_key, run_id, ctx)

    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = _CannotClose()
    await store.append(CTX.log_key, [_abandoned("r-0", TS - timedelta(minutes=10))], CTX)
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS, stale_run_after=timedelta(minutes=5))

    with caplog.at_level(logging.ERROR):
        events = [event async for event in runtime.run("Greeter", INPUT, CTX)]

    assert [event.kind for event in events][-1] == "run.completed"
    assert check_terminal(events) is None
    assert [event.kind for event in await store.read_run(CTX.log_key, "r-0", CTX)] == ["run.started"]
    assert "could not close abandoned run r-0" in caplog.text


def test_a_staleness_window_of_zero_is_refused() -> None:
    """Zero does not mean "off", it means "every run is abandoned the moment it opens" — the next
    caller's idea of now is already past the ts on a run.started stamped a moment ago, so one turn
    per session would go back to being a race. Refused at the boundary rather than documented."""
    with pytest.raises(ValidationError):
        RuntimeSettings.model_validate({"stale_run_after_seconds": 0})


async def test_the_staleness_window_comes_from_settings_when_it_is_not_passed_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default lives in settings, not in the Runtime: an operator whose turns are slower — or
    whose approvals are — changes it without touching a line of code."""
    monkeypatch.setenv("AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS", "0.001")
    reset_settings_cache()
    try:
        spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
        store = MemoryEventStore()
        await store.append(CTX.log_key, [_abandoned("r-0", TS - timedelta(seconds=1))], CTX)
        runtime = Runtime([StubEngine()], store, {spec.name: spec}, clock=lambda: TS)

        events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    finally:
        reset_settings_cache()

    assert events[-1].kind == "run.completed"
    assert [event.kind for event in await store.read_run(CTX.log_key, "r-0", CTX)] == ["run.started", "run.failed"]
