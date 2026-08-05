"""What one sink's bounded queue does when the sink cannot keep up: what it keeps, what it
drops, when it waits, and when it gives up on the sink entirely.

Every assertion is on counts and queue depth rather than on elapsed time — a bound that only
shows up as "fast enough" is not a bound.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentdeck.core.events import Event, TextDelta
from agentdeck.core.ports import EventSinkPort, SinkFullPolicy
from agentdeck.runtime.dispatch import DROP_LOG_INTERVAL, SinkDispatch

if TYPE_CHECKING:
    import pytest

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _event(seq: int) -> Event:
    return Event(
        kind="text.delta",
        seq=seq,
        run_id="r-1",
        session_id="s-1",
        tenant="acme",
        origin="Greeter",
        ts=TS,
        payload=TextDelta(message_id="m1", text=str(seq)),
    )


class Recorder(EventSinkPort):
    """Takes every event without complaint — the baseline the failure cases are measured against."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        await asyncio.sleep(0)
        self.events.append(event)

    def seqs(self) -> list[int]:
        return [event.seq for event in self.events]


class Gated(Recorder):
    """Stalls in ``emit`` until released, the way a wedged telemetry endpoint does."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def emit(self, event: Event) -> None:
        await self.release.wait()
        self.events.append(event)


class GatedBlocking(Gated):
    on_full = SinkFullPolicy.BLOCK


class Broken(EventSinkPort):
    """Fails every time, and counts how many times it was given the chance."""

    def __init__(self) -> None:
        self.calls = 0

    async def emit(self, event: Event) -> None:
        self.calls += 1
        raise RuntimeError("sink is down")


class BrokenBlocking(Broken):
    on_full = SinkFullPolicy.BLOCK


class Flaky(EventSinkPort):
    """Fails every other event: never twice in a row, so never broken."""

    def __init__(self) -> None:
        self.calls = 0

    async def emit(self, event: Event) -> None:
        self.calls += 1
        if self.calls % 2:
            raise RuntimeError("transient")


async def test_drain_flushes_everything_the_sink_has_not_taken_yet() -> None:
    sink = Recorder()
    dispatch = SinkDispatch(sink)
    for seq in range(5):
        await dispatch.submit(_event(seq))

    assert sink.seqs() == []  # the submits never waited for the sink
    await dispatch.drain()

    assert sink.seqs() == [0, 1, 2, 3, 4]
    assert (dispatch.dropped, dispatch.failed, dispatch.depth) == (0, 0, 0)


async def test_a_full_queue_drops_the_oldest_events_and_keeps_the_newest() -> None:
    sink = Recorder()
    dispatch = SinkDispatch(sink, capacity=2)
    for seq in range(6):
        await dispatch.submit(_event(seq))

    assert dispatch.depth == 2
    assert dispatch.dropped == 4
    await dispatch.drain()
    assert sink.seqs() == [4, 5]


async def test_a_stalling_sink_grows_neither_its_queue_nor_the_task_count() -> None:
    """The whole point: 200 events past a wedged sink cost a fixed backlog and one task."""
    sink = Gated()
    dispatch = SinkDispatch(sink, capacity=8)
    before = len(asyncio.all_tasks())

    for seq in range(200):
        await dispatch.submit(_event(seq))

    assert dispatch.depth == 8
    assert dispatch.dropped == 192
    assert len(asyncio.all_tasks()) == before + 1

    sink.release.set()
    await dispatch.drain()
    assert len(asyncio.all_tasks()) == before


async def test_a_blocking_sink_makes_the_producer_wait_instead_of_losing_an_event() -> None:
    sink = GatedBlocking()
    dispatch = SinkDispatch(sink, capacity=1)
    await dispatch.submit(_event(0))
    await dispatch.submit(_event(1))

    waiting = asyncio.create_task(dispatch.submit(_event(2)))
    for _ in range(3):
        await asyncio.sleep(0)  # every chance to finish, if it were going to
    assert not waiting.done()
    assert dispatch.dropped == 0

    sink.release.set()
    await waiting
    await dispatch.drain()
    assert sink.seqs() == [0, 1, 2]
    assert dispatch.dropped == 0


async def test_a_sink_that_keeps_failing_is_disabled_instead_of_retried_forever() -> None:
    sink = Broken()
    dispatch = SinkDispatch(sink, failure_limit=3)
    for seq in range(10):
        await dispatch.submit(_event(seq))
    await dispatch.drain()

    assert dispatch.disabled is True
    assert sink.calls == 3
    assert dispatch.failed == 3
    assert dispatch.dropped == 7  # the rest never reached a sink known to be broken

    await dispatch.submit(_event(10))
    assert sink.calls == 3
    assert dispatch.dropped == 8


async def test_a_sink_that_recovers_between_failures_is_never_disabled() -> None:
    """``failure_limit`` counts consecutive failures: an occasionally failing tap stays live."""
    sink = Flaky()
    dispatch = SinkDispatch(sink, failure_limit=2)
    for seq in range(10):
        await dispatch.submit(_event(seq))
    await dispatch.drain()

    assert dispatch.disabled is False
    assert sink.calls == 10
    assert dispatch.failed == 5
    assert dispatch.dropped == 0


async def test_disabling_a_blocking_sink_releases_the_producer_waiting_on_it() -> None:
    """Otherwise a sink that must not lose events would stall every run forever the moment
    it broke — a wedge worse than the drops it was configured to avoid."""
    sink = BrokenBlocking()
    dispatch = SinkDispatch(sink, capacity=1, failure_limit=1)

    async with asyncio.timeout(5):  # a guard, not a measurement: the point is that it returns
        for seq in range(3):
            await dispatch.submit(_event(seq))
        await dispatch.drain()

    assert dispatch.disabled is True
    assert sink.calls == 1
    assert dispatch.dropped + sink.calls == 3


async def test_dropped_and_failed_events_are_logged_not_only_counted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    sink = Gated()
    dispatch = SinkDispatch(sink, capacity=1)
    for seq in range(DROP_LOG_INTERVAL + 2):
        await dispatch.submit(_event(seq))
    sink.release.set()
    await dispatch.drain()

    logged = [record.getMessage() for record in caplog.records]
    drops = [message for message in logged if "dropped" in message]
    assert len(drops) == 2  # the first drop, then one every DROP_LOG_INTERVAL
    assert "seq=0" in drops[0]
    assert f"{DROP_LOG_INTERVAL} events lost so far" in drops[1]
    assert any(f"lost {dispatch.dropped} events" in message for message in logged)
