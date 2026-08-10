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
from event_log_checks import check_contiguous, check_terminal
from never_yields import NeverYields
from pydantic import ValidationError

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.composition import build_runtime
from agentdeck.core.content import DataBlock, TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    Event,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    RunStarted,
    TextDelta,
    Usage,
    UsageReported,
)
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import EventSinkPort, SessionClaim
from agentdeck.core.status import RunStatus, status_of
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


CTX = RunContext(namespace="acme", run_id="r-1", session_id="s-1")

# A wedge detector, not a budget: everything here is in-process and takes microseconds.
WEDGE_TIMEOUT = 5.0


def _runtime(*, sinks: list[EventSinkPort] | None = None) -> tuple[Runtime, MemoryEventStore]:
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    return Runtime([StubEngine()], store, {spec.name: spec}, sinks=sinks or []), store


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
        async for _ in runtime.run(
            "Nope", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
            pass
    assert await store.read(CTX.log_key, CTX) == []


async def test_an_invocable_whose_engine_is_not_registered_is_reported() -> None:
    spec = InvocableSpec(name="Ghost", kind=InvocableKind.AGENT, engine="temporal")
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec})
    with pytest.raises(NotFoundError, match="temporal"):
        async for _ in runtime.run(
            "Ghost", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
            pass


async def test_the_envelope_timestamp_comes_from_the_stores_clock() -> None:
    """The seam a test freezes time through is the store's, not the Runtime's: ``ts`` is assigned
    in the same step that persists the event (ADR-D11), so nothing above the store can decide it."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    runtime = Runtime([StubEngine()], MemoryEventStore(clock=lambda: TS), {spec.name: spec})
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    assert {event.ts for event in events} == {TS}


async def test_run_started_carries_what_the_run_was_asked_for() -> None:
    """No context snapshot: everything it held was recorded and read by nothing, so
    ``run.started`` carries the ask and the envelope carries where it ran."""
    runtime, _ = _runtime()
    opening = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=CTX.run_id, session_id=CTX.session_id, namespace=CTX.namespace
        )
    ][0]

    assert opening.payload.invocable == "Greeter"
    assert opening.payload.kind_of_invocable == "agent"
    assert opening.payload.input == INPUT
    assert (opening.run_id, opening.session_id, opening.namespace) == (CTX.run_id, CTX.session_id, CTX.namespace)


async def test_a_run_without_a_session_is_still_persisted_under_its_own_id() -> None:
    """Otherwise persist-before-yield would quietly not apply to one-off runs."""
    runtime, store = _runtime()
    ctx = replace(CTX, session_id=None)
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
        )
    ]
    assert await store.read("r-1", ctx) == events
    assert all(event.session_id is None for event in events)


async def test_sinks_see_every_event_without_the_run_waiting_for_them() -> None:
    recorder = Recorder()
    runtime, _ = _runtime(sinks=[recorder])
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
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
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, sinks=[recorder])

    opening = [
        event
        async for event in runtime.run(
            "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    resumed = [
        event
        async for event in runtime.resume(
            "Approver", "t1", "approved", run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    await runtime.drain()

    assert [event.kind for event in resumed] == ["run.resumed", "run.completed"]
    assert recorder.by_seq() == opening + resumed


async def test_a_failing_sink_does_not_fail_the_run_or_starve_the_others() -> None:
    recorder = Recorder()
    runtime, store = _runtime(sinks=[Broken(), recorder])
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    await runtime.drain()
    assert [event.kind for event in events][-1] == "run.completed"
    assert await store.read(CTX.log_key, CTX) == events
    assert recorder.by_seq() == events


async def test_drain_waits_for_the_sink_emits_still_in_flight() -> None:
    """Without it, pending emits die with the event loop and the last audit events vanish."""
    recorder = Recorder()
    runtime, _ = _runtime(sinks=[recorder])
    async for _ in runtime.run(
        "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
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
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
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
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
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
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, sinks=[recorder])

    events = [
        event
        async for event in runtime.run(
            "Firehose", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
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
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec}, sinks=[stalled])

    before = len(asyncio.all_tasks())
    events = [
        event
        async for event in runtime.run(
            "Chatty", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

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
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

    seen: list[Event] = []
    with pytest.raises(ValueError, match="secret detail"):
        async for event in runtime.run(
            "Boom", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
            seen.append(event)

    assert [event.kind for event in seen] == ["run.started", "text.delta", "run.failed"]
    assert seen[-1].payload.error_code == "engine_error"
    assert seen[-1].payload.retryable is False
    assert await store.read(CTX.log_key, CTX) == seen


async def test_run_failed_names_the_exception_type_and_not_its_message() -> None:
    """An exception message can carry request content or a secret; sinks must not receive it."""
    spec = stub_spec("Leaky", ValueError("sk-live-abc123"))
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec})

    seen: list[Event] = []
    with pytest.raises(ValueError):
        async for event in runtime.run(
            "Leaky", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
            seen.append(event)

    assert "sk-live-abc123" not in seen[-1].payload.message
    assert "ValueError" in seen[-1].payload.message


async def test_an_engine_that_stops_without_a_terminal_event_gets_one_anyway() -> None:
    """A silent engine would leave every consumer waiting forever."""
    spec = stub_spec("Quitter", TextDelta(message_id="m1", text="and then nothing"))
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec})
    events = [
        event
        async for event in runtime.run(
            "Quitter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
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
    runtime = Runtime([Nosy()], store, {spec.name: spec})

    first = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    async for _ in runtime.run("Greeter", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace):
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
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

    events = [
        event
        async for event in runtime.run(
            "Chatterbox", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    assert [event.kind for event in events] == ["run.started", "run.completed"]
    assert check_terminal(events) is None
    assert await store.read(CTX.log_key, CTX) == events


async def test_an_abandoned_run_is_closed_in_the_log() -> None:
    """A consumer that walks away leaves a run that will never produce another event; without a
    terminal one, a later reader cannot tell it from a run still in flight."""
    spec = stub_spec("Chatty", *[TextDelta(message_id="m1", text=str(n)) for n in range(3)], DONE)
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

    async with aclosing(
        runtime.run("Chatty", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace)
    ) as run:
        async for _ in run:
            break

    stored = await store.read(CTX.log_key, CTX)
    assert [event.kind for event in stored] == ["run.started", "run.cancelled"]
    assert check_terminal(stored) is None
    assert stored[-1].payload.reason == "consumer stopped reading"


async def test_a_completed_run_is_not_cancelled_when_its_consumer_lets_go() -> None:
    """The close path must not fire on a run that already ended — that would be two terminals."""
    runtime, store = _runtime()
    async with aclosing(
        runtime.run("Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace)
    ) as run:
        async for _ in run:
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)][-1] == "run.completed"


async def test_a_suspended_run_is_not_cancelled_when_its_consumer_lets_go() -> None:
    """An interrupted run is legitimately waiting; closing the stream must not close the run."""
    spec = stub_spec("Approver", RunInterrupted(interrupt_id="i1", reason="approval", payload={}))
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

    async with aclosing(
        runtime.run("Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace)
    ) as run:
        async for _ in run:
            pass

    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started", "run.interrupted"]


async def test_an_invocable_with_no_script_is_a_config_error() -> None:
    """A misconfigured invocable is the caller's mistake, not a run that failed."""
    spec = InvocableSpec(name="Empty", kind=InvocableKind.AGENT, engine="stub", native=None)
    runtime = Runtime([StubEngine()], MemoryEventStore(), {spec.name: spec})

    with pytest.raises(ConfigError, match="no stub script"):
        async for _ in runtime.run(
            "Empty", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
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
    when the next turn asks about them — staleness without a test waiting for a wall clock.

    Handed to the *store*, which is what stamps events and what subtracts ``stale_after`` from
    its own now; the Runtime holds no clock any more.
    """

    def __init__(self) -> None:
        self._at = TS

    def __call__(self) -> datetime:
        self._at += timedelta(seconds=1)
        return self._at


class _Held:
    """A clock a test moves by hand, for arranging an event's age.

    The store stamps ``ts``, so backdating an event means holding this clock in the past while
    the event is written and then bringing it forward — which is what a run left open ten
    minutes ago and never closed actually looks like to the next turn.
    """

    def __init__(self, at: datetime = TS) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _abandoned(origin: str = "Ghost") -> RunStarted:
    """The opening of a run left open by a process that died: nothing else produces one, because
    a Runtime that exits at all closes its own run in the log."""
    context = None
    return RunStarted(invocable=origin, kind_of_invocable="agent", input=INPUT, context=context)


async def _leave_open(store: MemoryEventStore, clock: _Held, run_id: str, age: timedelta) -> None:
    """Write one run's ``run.started`` ``age`` in the past and leave it at that, then return the
    store's clock to ``TS`` so the next turn judges the run from there."""
    clock.at = TS - age
    await store.append(CTX.log_key, [_abandoned()], replace(CTX, run_id=run_id), "Ghost")
    clock.at = TS


async def test_a_turn_arriving_while_another_is_in_flight_is_refused() -> None:
    """One session, one turn. The refusal names the session and the run holding it, and the turn
    that already had it runs on untouched."""
    engine = _Blocking()
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([engine], store, {spec.name: spec})

    async def _play(run_id: str) -> list[Event]:
        return [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=run_id, session_id=CTX.session_id, namespace=CTX.namespace
            )
        ]

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
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

    assert [
        event
        async for event in runtime.run(
            "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ][-1].kind == "run.interrupted"
    with pytest.raises(SessionBusyError, match="r-1"):
        async for _ in runtime.run("Approver", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace):
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started", "run.interrupted"]


async def test_a_turn_after_the_previous_one_finished_is_not_refused() -> None:
    """The ordinary case the claim must leave alone: a conversation is a sequence of turns."""
    runtime, store = _runtime()
    first = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]
    second = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace
        )
    ]

    assert first[-1].kind == "run.completed"
    assert second[-1].kind == "run.completed"
    assert await store.read(CTX.log_key, CTX) == first + second


async def test_two_runs_without_a_session_never_contend() -> None:
    """A sessionless run is its own log, so there is nobody in it to be busy: two at once share
    no conversation and must both play."""
    engine = _Blocking()
    engine.release.set()
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    runtime = Runtime([engine], MemoryEventStore(), {spec.name: spec})

    async def _play(run_id: str) -> list[Event]:
        ctx = replace(CTX, run_id=run_id, session_id=None)
        return [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
            )
        ]

    both = await asyncio.gather(_play("r-1"), _play("r-2"))
    assert [events[-1].kind for events in both] == ["run.completed", "run.completed"]


async def test_a_turn_takes_over_a_session_whose_run_went_silent_and_closes_it_as_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The hard-kill case: the run holding this session stopped writing long enough ago that
    nothing is coming back for it. The new turn proceeds, the abandoned run is closed under its
    own name and ``seq``, and the takeover is on the record — it may always be premature."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    clock = _Held()
    store = MemoryEventStore(clock=clock)
    await _leave_open(store, clock, "r-0", timedelta(minutes=10))
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, stale_run_after=timedelta(minutes=5))

    with caplog.at_level(logging.WARNING):
        events = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
            )
        ]

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
    clock = _Held()
    store = MemoryEventStore(clock=clock)
    await _leave_open(store, clock, "r-0", timedelta(minutes=1))
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, stale_run_after=timedelta(minutes=5))

    with pytest.raises(SessionBusyError, match="r-0"):
        async for _ in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        ):
            pass
    assert [event.kind for event in await store.read(CTX.log_key, CTX)] == ["run.started"]


async def test_a_run_that_writes_again_after_being_taken_over_lands_behind_its_terminal_event() -> None:
    """The cost of a takeover that was premature, and what it is now. The run this turn stepped
    over was alive after all, and it writes again after the closing ``run.failed``.

    It no longer collides with anything. Nothing outside the store holds a number, so the
    resurrected writer is handed the seq *after* the closing event rather than one it already
    spent — which is why its writes go through instead of failing on a spent seq (ADR-D11). The
    log stays dense and no seq answers to two events, both of which are now structural.

    What detects the resurrection is ``check_terminal``: the run has events past a terminal one,
    which no healthy run ever does. That is the bound on the damage — a premature takeover leaves
    a coherently numbered log with an unmistakable shape, rather than two different events at one
    ``seq``, which nothing in the log could reveal.
    """
    engine = _Stalling("r-1")
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    # The ticking clock is the store's: every event it stamps is a second older than the last, so
    # the quiet run's own writes age it past the window without the test waiting for anything.
    store = MemoryEventStore(clock=_Ticking())
    runtime = Runtime([engine], store, {spec.name: spec}, stale_run_after=timedelta(seconds=1))

    quiet = asyncio.create_task(_collect(runtime, "r-1"))
    async with asyncio.timeout(WEDGE_TIMEOUT):
        await engine.quiet.wait()
        taken = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace
            )
        ]
        engine.release.set()
        await quiet

    assert taken[-1].kind == "run.completed"
    resurrected = await store.read_run(CTX.log_key, "r-1", CTX)
    pairs = [(event.run_id, event.seq) for event in resurrected]
    assert len(set(pairs)) == len(pairs), f"a seq answers to two events: {[(e.seq, e.kind) for e in resurrected]}"
    assert check_contiguous(resurrected) == []
    assert [event.kind for event in resurrected] == ["run.started", "text.delta", "run.failed", "run.completed"]
    assert check_terminal(resurrected) == "2 terminal events: ['run.failed', 'run.completed']"


async def test_a_run_resurrected_into_an_interrupt_takes_its_session_back() -> None:
    """The worse half of the same premature takeover: the resurrected run's next write is not a
    terminal event but ``run.interrupted``, which is a run *waiting*, not a run finished.

    So it does not merely land behind its own ``run.failed`` — it becomes ``WAITING_HUMAN`` and
    takes the session back, after a turn was told the session was free and used it. The session
    ends up held by the run that was declared dead, and it is listed as pending, meaning a
    resume can be addressed to a run whose log says it was abandoned.

    This is arguably the *right* outcome — the run really is alive and really is waiting — and it
    is why the takeover stays advisory rather than fatal. It is pinned because it is the shape a
    reader of ADR-D11 would not predict: the ADR says a spent seq can no longer refuse a write,
    and says nothing about a refused run reclaiming the session it was evicted from.
    ``check_terminal`` is what still gives it away.
    """
    engine = _Stalling("r-1")
    spec = stub_spec(
        "Approver",
        TextDelta(message_id="m1", text="thinking"),
        RunInterrupted(interrupt_id="i1", reason="approval", payload={}, thread_id="t1"),
        kind=InvocableKind.WORKFLOW,
    )
    store = MemoryEventStore(clock=_Ticking())
    runtime = Runtime([engine], store, {spec.name: spec}, stale_run_after=timedelta(seconds=1))

    quiet = asyncio.create_task(_collect(runtime, "r-1", "Approver"))
    async with asyncio.timeout(WEDGE_TIMEOUT):
        await engine.quiet.wait()
        taken = [
            event
            async for event in runtime.run(
                "Approver", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace
            )
        ]
        engine.release.set()
        await quiet

    assert taken[-1].kind == "run.interrupted"
    resurrected = await store.read_run(CTX.log_key, "r-1", CTX)
    assert [event.kind for event in resurrected] == ["run.started", "text.delta", "run.failed", "run.interrupted"]
    assert check_contiguous(resurrected) == [], "the log stays dense however wrong its shape is"
    assert check_terminal(resurrected) == "terminal event 'run.failed' at index 2 of 4, not last"
    assert await store.run_status(CTX.log_key, "r-1", CTX) is RunStatus.WAITING_HUMAN
    assert "r-1" in [run.run_id for run in await runtime.pending(namespace=CTX.namespace)], (
        "the evicted run is resumable again"
    )


async def _collect(runtime: Runtime, run_id: str, name: str = "Greeter") -> list[Event]:
    return [
        event
        async for event in runtime.run(name, INPUT, run_id=run_id, session_id=CTX.session_id, namespace=CTX.namespace)
    ]


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
            self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
        ) -> tuple[SessionClaim, Event | None]:
            claimed = await super().claim_start(log_key, opening, ctx, origin, stale_after)
            if ctx.run_id == "r-1":
                self.committed.set()
                await asyncio.Event().wait()  # a cancellation is the only way out, which is the point
            return claimed

    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = _SlowClaim()
    runtime = Runtime([StubEngine()], store, {spec.name: spec})

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
        next_turn = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id="r-2", session_id=CTX.session_id, namespace=CTX.namespace
            )
        ]
        assert next_turn[-1].kind == "run.completed"


async def test_a_takeover_whose_bookkeeping_fails_still_leaves_this_turn_runnable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The claim is committed before the abandoned run is closed, so a store failure in between
    must not escape: this run would be left open with no terminal event, holding the session it
    just took for a whole window. The close is dropped, reported, and left to the next turn."""

    class _CannotClose(MemoryEventStore):
        """Refuses only the closing write, which the takeover makes in the abandoned run's own
        context — that context is the one thing distinguishing it from this turn's own appends."""

        def __init__(self, clock: _Held) -> None:
            super().__init__(clock=clock)
            self.seeded = False

        async def append(
            self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str
        ) -> list[Event]:
            if self.seeded and ctx.run_id == "r-0":
                raise StoreError("the log went away mid-takeover")
            return await super().append(log_key, payloads, ctx, origin)

    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    clock = _Held()
    store = _CannotClose(clock)
    await _leave_open(store, clock, "r-0", timedelta(minutes=10))
    store.seeded = True
    runtime = Runtime([StubEngine()], store, {spec.name: spec}, stale_run_after=timedelta(minutes=5))

    with caplog.at_level(logging.ERROR):
        events = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
            )
        ]

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
    """The default lives in settings, resolved by ``build_runtime`` — not in ``Runtime`` itself
    (issue #155: the bare constructor takes no ambient configuration at all, so a caller who
    wants the configured window builds through ``build_runtime``, the same as its other
    adapters). An operator whose turns are slower — or whose approvals are — changes it without
    touching a line of code.

    A whole minute, against a run left open ten minutes ago: staleness is forced by the timestamp,
    never by shrinking the window towards the latency of a claim, which is how a test would come to
    depend on how loaded the machine is. The shipped default is an hour, so a takeover happening at
    all is what proves the environment's minute was the window this Runtime used.
    """
    monkeypatch.setenv("AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS", "60")
    reset_settings_cache()
    try:
        spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
        clock = _Held()
        store = MemoryEventStore(clock=clock)
        await _leave_open(store, clock, "r-0", timedelta(minutes=10))
        runtime = build_runtime(engines=[StubEngine()], invocables={spec.name: spec}, store=store, sinks=())

        events = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
            )
        ]
    finally:
        reset_settings_cache()

    assert events[-1].kind == "run.completed"
    assert [event.kind for event in await store.read_run(CTX.log_key, "r-0", CTX)] == ["run.started", "run.failed"]


class _Reporting(StubEngine):
    """An engine whose run reports on itself between payloads — a stand-in for a tool or a node,
    which report from inside an engine and have no way to yield an event of their own."""

    def __init__(self, before: int = 1) -> None:
        self._before = before

    async def start(
        self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        played = 0
        async for payload in super().start(spec, input, history, ctx):
            if played == self._before:
                await ctx.reporter.status("Searching GitHub")
                await ctx.reporter.progress("Reviewing issues", current=2, total=4)
            played += 1
            yield payload

    async def resume(
        self, spec: InvocableSpec, thread_id: str, value: object, ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        await ctx.reporter.status("Searching GitHub")
        await ctx.reporter.progress("Reviewing issues", current=2, total=4)
        async for payload in super().resume(spec, thread_id, value, ctx):
            yield payload


async def test_a_run_that_reports_gets_its_reports_in_the_stream_in_order() -> None:
    """The feature at the Runtime: a report made from inside the engine becomes an event on the
    run's own stream, in the order it was made, ahead of the payload that followed it."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([_Reporting()], store, {spec.name: spec})

    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

    assert [event.kind for event in events] == [
        "run.started",
        "text.delta",
        "status.reported",
        "progress.reported",
        "run.completed",
    ]
    assert events[2].payload.message == "Searching GitHub"
    assert (events[3].payload.step, events[3].payload.current, events[3].payload.total) == ("Reviewing issues", 2, 4)
    # Stamped like every other event — same run, same origin, contiguous seq — and persisted
    # before it was yielded, which is what lets a consumer refetch one it missed.
    assert {event.origin for event in events} == {"Greeter"}
    assert check_contiguous(events) == [] and check_terminal(events) is None
    assert await store.read(CTX.log_key, CTX) == events


async def test_a_report_made_during_the_last_thing_a_run_did_lands_before_the_terminal_event() -> None:
    """Nothing may follow a terminal event into the log, so a report races it or loses: it goes
    in front of ``run.completed``, never after."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([_Reporting(before=1)], store, {spec.name: spec})

    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

    assert [event.kind for event in events[-2:]] == ["progress.reported", "run.completed"]
    assert check_terminal(await store.read(CTX.log_key, CTX)) is None


async def test_a_reporting_run_still_folds_to_the_status_its_lifecycle_says() -> None:
    """What the two kinds not being lifecycle kinds buys, on a real run: the store's own status
    projection — the thing a listing and a resume claim both read — is unmoved by them."""
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    runtime = Runtime([_Reporting()], store, {spec.name: spec})

    async for _ in runtime.run(
        "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass

    log = await store.read(CTX.log_key, CTX)
    assert [event.kind for event in log if event.kind.endswith(".reported")] != []  # it did report
    assert status_of(log) is RunStatus.COMPLETED
    assert [summary.run_id for summary in await store.list_runs(CTX, status=RunStatus.COMPLETED)] == ["r-1"]


async def test_two_concurrent_runs_never_drain_each_others_reports() -> None:
    """One buffer per run, bound by the Runtime: a report belongs to the run that made it, which
    is exactly the isolation a Runtime-wide buffer would silently lose."""
    entered = asyncio.Event()
    release = asyncio.Event()

    class _Interleaved(StubEngine):
        async def start(
            self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
        ) -> AsyncGenerator[KnownPayload, None]:
            await ctx.reporter.status(f"working on {ctx.run_id}")
            if ctx.run_id == "r-1":
                entered.set()
                await release.wait()
            yield DONE

    spec = stub_spec("Greeter")
    store = MemoryEventStore()
    runtime = Runtime([_Interleaved()], store, {spec.name: spec})

    async def drive(run_id: str, session_id: str) -> list[Event]:
        ctx = replace(CTX, run_id=run_id, session_id=session_id)
        return [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
            )
        ]

    first = asyncio.create_task(drive("r-1", "s-1"))
    await asyncio.wait_for(entered.wait(), WEDGE_TIMEOUT)
    second = await drive("r-2", "s-2")
    release.set()
    reported = await asyncio.wait_for(first, WEDGE_TIMEOUT)

    assert [event.payload.message for event in reported if event.kind == "status.reported"] == ["working on r-1"]
    assert [event.payload.message for event in second if event.kind == "status.reported"] == ["working on r-2"]


async def test_a_resumed_run_can_report_too() -> None:
    """``resume`` binds the same channel ``run`` does — otherwise the second half of an
    interrupted run would go quiet for no reason a caller could see."""
    interrupt = RunInterrupted(interrupt_id="i-1", reason="human", payload={"q": "ok?"}, thread_id="t-1")
    spec = stub_spec("Asker", interrupt, DONE)
    store = MemoryEventStore()
    runtime = Runtime([_Reporting()], store, {spec.name: spec})

    async for _ in runtime.run(
        "Asker", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    resumed = [
        event
        async for event in runtime.resume(
            "Asker", "t-1", "yes", run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

    assert [event.kind for event in resumed] == ["run.resumed", "status.reported", "progress.reported", "run.completed"]
    log = await store.read(CTX.log_key, CTX)
    assert check_contiguous(log) == [] and check_terminal(log) is None


async def test_a_caller_built_context_reports_into_nothing() -> None:
    """Existing runs behave unchanged when no updates are emitted: a context nobody bound
    still accepts a report, drops it, and produces exactly the run it produced before."""
    ctx = replace(CTX, run_id="r-9")
    await ctx.reporter.status("into the void")

    spec = stub_spec("Greeter", DONE)
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {spec.name: spec})
    events = [
        event
        async for event in runtime.run(
            "Greeter", INPUT, run_id=(ctx).run_id, session_id=(ctx).session_id, namespace=(ctx).namespace
        )
    ]

    assert [event.kind for event in events] == ["run.started", "run.completed"]


async def test_a_store_that_refuses_a_report_costs_the_report_not_the_run(caplog) -> None:
    """An advisory event is not worth a run. Every store today keeps events as opaque JSON, so a
    store that dislikes one *kind* is a future rather than a bug — but it is the future this
    change's own ledger cites (#101), and without this arm it turns a run that would have
    completed into ``run.failed``.

    And it costs the report *only*. The refused append never got as far as taking a number, so the
    log the run leaves behind is dense: this is the gap ADR-D11 exists to remove, and the one
    assertion that says the whole port change worked. Before it, the same run left
    ``check_contiguous == [2]`` — a hole no consumer's refetch could ever fill, indistinguishable
    from an event lost in transit.
    """

    class _RefusesReports(MemoryEventStore):
        async def append(
            self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str
        ) -> list[Event]:
            if any(payload.kind == "status.reported" for payload in payloads):
                raise StoreError("this store has never heard of status.reported")
            return await super().append(log_key, payloads, ctx, origin)

    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = _RefusesReports()
    runtime = Runtime([_Reporting()], store, {spec.name: spec})

    with caplog.at_level(logging.WARNING):
        events = [
            event
            async for event in runtime.run(
                "Greeter", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
            )
        ]

    # The run is untouched: it completes, and the report that survived is still in it.
    assert [event.kind for event in events] == ["run.started", "text.delta", "progress.reported", "run.completed"]
    assert check_terminal(events) is None
    assert status_of(await store.read(CTX.log_key, CTX)) is RunStatus.COMPLETED
    assert "could not record its status.reported" in caplog.text
    # No gap where the dropped report would have been: a seq is allocated inside the write, so a
    # refused write takes no number, and a hole in this log can only mean an event was truly lost.
    assert check_contiguous(await store.read(CTX.log_key, CTX)) == []


def _approver() -> tuple[Runtime, MemoryEventStore]:
    """A workflow that suspends once, so a resume can be claimed against it."""
    spec = stub_spec(
        "Approver",
        RunInterrupted(interrupt_id="i1", reason="approval", payload={}, thread_id="t1"),
        DONE,
        kind=InvocableKind.WORKFLOW,
    )
    store = MemoryEventStore()
    return Runtime([StubEngine()], store, {spec.name: spec}), store


async def test_the_answer_is_in_the_log_before_the_engine_has_been_asked_for_anything() -> None:
    """The window that used to lose a resume value for good: the claim is committed, the run
    reads ``RUNNING``, and the engine has not been started yet — which is the instant a process
    dies. What the log holds here is all a successor gets, so it has to hold the answer.
    """
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass

    resuming = runtime.resume(
        "Approver",
        "t1",
        {"approved": True, "note": "ship it"},
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    )
    claim = await anext(resuming)  # the engine is only started after this is yielded

    logged = await store.read(CTX.log_key, CTX)
    assert status_of(logged) is RunStatus.RUNNING
    assert logged[-1] == claim
    assert claim.payload.value == [DataBlock(data={"approved": True, "note": "ship it"})]
    await resuming.aclose()


async def test_a_text_answer_is_recorded_the_way_a_turn_s_own_input_is() -> None:
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    async for _ in runtime.resume(
        "Approver", "t1", "approved", run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass

    resumed = next(event for event in await store.read(CTX.log_key, CTX) if event.kind == "run.resumed")
    assert resumed.payload.value == [TextBlock(text="approved")]


async def test_an_answer_already_in_blocks_is_recorded_as_those_blocks() -> None:
    """A caller answering an inbox in the field's own type must not have it wrapped in a data
    block: the same approval would then be two shapes depending on how it was spelled."""
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    async for _ in runtime.resume(
        "Approver",
        "t1",
        [TextBlock(text="approved")],
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    ):
        pass

    resumed = next(event for event in await store.read(CTX.log_key, CTX) if event.kind == "run.resumed")
    assert resumed.payload.value == [TextBlock(text="approved")]


async def test_an_empty_array_answer_is_data_not_content_with_no_blocks() -> None:
    """ "Nothing selected" is an answer. Recorded as content it would read as no blocks at all,
    which is indistinguishable from a resume that answered nothing — and unreconstructable."""
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    async for _ in runtime.resume(
        "Approver", "t1", [], run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass

    resumed = next(event for event in await store.read(CTX.log_key, CTX) if event.kind == "run.resumed")
    assert resumed.payload.value == [DataBlock(data=[])]


async def test_a_resume_with_nothing_to_answer_records_no_value() -> None:
    """Lifting an operator's pause answers no question, and an empty content list would claim
    that an answer arrived and was blank."""
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    async for _ in runtime.resume(
        "Approver", "t1", None, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass

    resumed = next(event for event in await store.read(CTX.log_key, CTX) if event.kind == "run.resumed")
    assert resumed.payload.value is None


class Elementwise:
    """An array-like answer, the shape a data workflow's state actually carries: comparing it
    returns per-element results, and asking whether that is true raises."""

    def __eq__(self, other: object) -> Elementwise:  # ty: ignore[invalid-return-type]
        return self

    def __ne__(self, other: object) -> Elementwise:  # ty: ignore[invalid-return-type]
        return self

    def __bool__(self) -> bool:
        raise ValueError("the truth value of an array with more than one element is ambiguous")

    __hash__ = None  # type: ignore[assignment]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(object(), id="not-json"),
        pytest.param({"ratio": float("nan")}, id="non-finite-float"),
        pytest.param(datetime(2026, 8, 6, tzinfo=UTC), id="datetime"),
        pytest.param(Elementwise(), id="array-like"),
    ],
)
async def test_an_answer_the_log_cannot_hold_is_reported_rather_than_failing_the_resume(
    value: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The declared ceiling: JSON is what the log can carry, so a value outside it records
    nothing — and says which run, because a silent skip leaves the log looking exactly like the
    bug this field fixed."""
    runtime, store = _approver()
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    with caplog.at_level(logging.WARNING):
        events = [
            event
            async for event in runtime.resume(
                "Approver", "t1", value, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
            )
        ]

    assert [event.kind for event in events] == ["run.resumed", "run.completed"]
    resumed = next(event for event in await store.read(CTX.log_key, CTX) if event.kind == "run.resumed")
    assert resumed.payload.value is None
    assert "the answer for run r-1 is a" in caplog.text
