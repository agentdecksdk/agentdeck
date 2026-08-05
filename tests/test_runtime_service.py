"""Runtime behavior the contract suite can't state as an engine invariant: lookup failures,
sink fan-out, and where a sessionless run's events land.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    RunCompleted,
    RunFailed,
    RunInterrupted,
    TextDelta,
    Usage,
    UsageReported,
    check_terminal,
)
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import EventSinkPort
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.events import Event, KnownPayload

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
INPUT = [TextBlock(text="hi")]
DONE = RunCompleted(output=[TextBlock(text="hi back")], usage=Usage(input_tokens=1, output_tokens=2))


CTX = RunContext(tenant="acme", principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


def _runtime(*, sinks: list[EventSinkPort] | None = None) -> tuple[Runtime, MemoryEventStore]:
    spec = stub_spec("Greeter", TextDelta(message_id="m1", text="hi back"), DONE)
    store = MemoryEventStore()
    return Runtime([StubEngine()], store, {spec.name: spec}, sinks=sinks or [], clock=lambda: TS), store


class Recorder(EventSinkPort):
    """A sink that awaits before recording, so it can only pass if the port's promise holds:
    every event arrives, in no particular order."""

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
