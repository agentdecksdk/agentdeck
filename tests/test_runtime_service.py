"""Runtime behavior the contract suite can't state as an engine invariant: lookup failures,
sink fan-out, and where a sessionless run's events land.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunCompleted, TextDelta, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import EventSinkPort
from agentdeck.errors import NotFoundError
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

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
    """A sink that just remembers what it was given."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.arrived = asyncio.Event()

    async def emit(self, event: Event) -> None:
        self.events.append(event)
        if event.kind == "run.completed":
            self.arrived.set()


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
    runtime, store = _runtime(sinks=[recorder])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    # the run finished without awaiting the sink; the sink catches up right after
    await asyncio.wait_for(recorder.arrived.wait(), timeout=1)
    assert recorder.events == events


async def test_a_failing_sink_does_not_fail_the_run() -> None:
    recorder = Recorder()
    runtime, store = _runtime(sinks=[Broken(), recorder])
    events = [event async for event in runtime.run("Greeter", INPUT, CTX)]
    await asyncio.wait_for(recorder.arrived.wait(), timeout=1)
    assert [event.kind for event in events][-1] == "run.completed"
    assert await store.read(CTX.log_key, CTX) == events
    assert recorder.events == events


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
        ) -> AsyncIterator[KnownPayload]:
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
