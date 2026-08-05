"""What one sink's bounded queue does when the sink cannot keep up: what it keeps, what it
drops, and when it gives up on the sink entirely. What it never does is wait for it.

Every assertion is on counts and queue depth rather than on elapsed time — a bound that only
shows up as "fast enough" is not a bound.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from agentdeck.core.events import Event, TextDelta
from agentdeck.core.ports import EventSinkPort
from agentdeck.runtime.dispatch import LOG_INTERVAL, SinkDispatch

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


class Broken(EventSinkPort):
    """Fails every time, and counts how many times it was given the chance."""

    def __init__(self) -> None:
        self.calls = 0

    async def emit(self, event: Event) -> None:
        self.calls += 1
        raise RuntimeError("sink is down")


class Flaky(EventSinkPort):
    """Fails every other event: never twice in a row, so never broken."""

    def __init__(self) -> None:
        self.calls = 0

    async def emit(self, event: Event) -> None:
        self.calls += 1
        if self.calls % 2:
            raise RuntimeError("transient")


class CancelOnce(Recorder):
    """Lets one ``CancelledError`` escape ``emit``, the way a leaked inner timeout scope does.

    Nobody cancelled anything: the sink is simply misbehaving, and the dispatch is expected to
    tell that apart from a cancellation it asked for.
    """

    def __init__(self) -> None:
        super().__init__()
        self.raised = False

    async def emit(self, event: Event) -> None:
        if not self.raised:
            self.raised = True
            raise asyncio.CancelledError
        await super().emit(event)


class SelfCancelOnce(Recorder):
    """Gets its consumer genuinely cancelled once from inside ``emit``.

    A real cancellation, unlike ``CancelOnce``'s bare raise: it must propagate, which kills the
    consumer task without raising anything the dispatch's callers ever see.
    """

    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def emit(self, event: Event) -> None:
        if not self.cancelled:
            self.cancelled = True
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)  # where the cancellation is delivered
        await super().emit(event)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


async def test_close_flushes_everything_the_sink_has_not_taken_yet() -> None:
    sink = Recorder()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):  # a hang guard, not a measurement
        for seq in range(5):
            await dispatch.submit(_event(seq))

        assert sink.seqs() == []  # the submits never waited for the sink
        await dispatch.close()

    assert sink.seqs() == [0, 1, 2, 3, 4]
    assert (dispatch.dropped, dispatch.failed, dispatch.depth) == (0, 0, 0)


async def test_a_sink_that_keeps_up_loses_nothing_however_fast_the_producer_is() -> None:
    """A run can fill the queue without the loop ever turning, since nothing on the event path
    has to suspend. Dropping then would punish the producer's speed, not the sink's."""
    sink = Recorder()
    dispatch = SinkDispatch(sink, capacity=4)
    async with asyncio.timeout(10):
        for seq in range(1000):
            await dispatch.submit(_event(seq))
        await dispatch.close()

    assert dispatch.dropped == 0
    assert sink.seqs() == list(range(1000))


async def test_a_full_queue_drops_the_oldest_events_and_keeps_the_newest() -> None:
    sink = Gated()
    dispatch = SinkDispatch(sink, capacity=2)
    for seq in range(6):
        await dispatch.submit(_event(seq))

    assert dispatch.depth == 2
    assert dispatch.dropped == 3  # seq 0 is in flight, 1..3 were displaced, 4 and 5 are queued
    sink.release.set()
    await dispatch.close()
    assert sink.seqs() == [0, 4, 5]


async def test_a_stalling_sink_grows_neither_its_queue_nor_the_task_count() -> None:
    """The whole point: 200 events past a wedged sink cost a fixed backlog and one task."""
    sink = Gated()
    dispatch = SinkDispatch(sink, capacity=8)
    before = len(asyncio.all_tasks())

    async with asyncio.timeout(10):
        for seq in range(200):
            await dispatch.submit(_event(seq))

        assert dispatch.depth == 8
        assert dispatch.dropped == 191  # 200 less the 8 queued and the one the sink is stuck on
        assert len(asyncio.all_tasks()) == before + 1

        sink.release.set()
        await dispatch.close()
    assert len(asyncio.all_tasks()) == before


async def test_a_sink_that_keeps_failing_is_disabled_instead_of_retried_forever() -> None:
    sink = Broken()
    dispatch = SinkDispatch(sink, failure_limit=3)
    async with asyncio.timeout(10):  # a hang guard, not a measurement
        for seq in range(10):
            await dispatch.submit(_event(seq))
        await dispatch.flush()  # not ``close``: the submit below has to be refused for being disabled

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
    await dispatch.close()

    assert dispatch.disabled is False
    assert sink.calls == 10
    assert dispatch.failed == 5
    assert dispatch.dropped == 0


async def test_dropped_events_are_logged_not_only_counted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    sink = Gated()
    dispatch = SinkDispatch(sink, capacity=1)
    for seq in range(LOG_INTERVAL + 2):
        await dispatch.submit(_event(seq))
    sink.release.set()
    await dispatch.close()

    logged = [record.getMessage() for record in caplog.records]
    drops = [message for message in logged if "dropped" in message]
    assert dispatch.dropped == LOG_INTERVAL
    assert len(drops) == 2  # the first drop, then one every LOG_INTERVAL
    assert "seq=1" in drops[0]
    assert f"{LOG_INTERVAL} events lost so far" in drops[1]
    assert any(f"lost {dispatch.dropped} events" in message for message in logged)


async def test_a_sink_failing_on_every_event_is_logged_once_per_streak_not_once_per_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A thousand copies of one stack trace is how a real incident gets missed."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    dispatch = SinkDispatch(Broken(), failure_limit=LOG_INTERVAL * 2)
    async with asyncio.timeout(10):
        for seq in range(LOG_INTERVAL + 5):
            await dispatch.submit(_event(seq))
        await dispatch.close()

    tracebacks = [record for record in caplog.records if record.exc_info is not None]
    assert dispatch.failed == LOG_INTERVAL + 5
    assert len(tracebacks) == 1
    assert any(f"failed {LOG_INTERVAL} emits" in record.getMessage() for record in caplog.records)


async def test_a_consumer_killed_by_a_real_cancellation_is_replaced() -> None:
    """A cancellation delivered inside ``emit`` kills the consumer task without failing anything
    here; a queue with nobody reading it would swallow every later event in silence."""
    sink = SelfCancelOnce()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        for _ in range(3):
            await asyncio.sleep(0)  # the consumer takes seq 0 and dies on it

        await dispatch.submit(_event(1))
        await dispatch.close()

    assert sink.cancelled is True
    assert sink.seqs() == [1]  # seq 0 died with the consumer; seq 1 proves a new one took over


async def test_close_does_not_hang_when_the_consumer_is_gone(caplog: pytest.LogCaptureFixture) -> None:
    """``Runtime.drain()`` closes every sink at once, so a queue that can never empty would
    cost every other sink its tail — and the process its shutdown."""
    caplog.set_level(logging.ERROR, logger="agentdeck.runtime.dispatch")
    sink = SelfCancelOnce()
    dispatch = SinkDispatch(sink, capacity=4)
    await dispatch.submit(_event(0))
    await dispatch.submit(_event(1))
    for _ in range(3):
        await asyncio.sleep(0)  # seq 0 kills the consumer, leaving seq 1 queued forever

    async with asyncio.timeout(10):  # the hang this test exists for
        await dispatch.close()

    assert sink.seqs() == []
    assert dispatch.depth == 1
    assert dispatch.dropped == 2  # seq 0 went down with the consumer, seq 1 was never taken
    assert _errors(caplog) == ["sink SelfCancelOnce has no consumer left; 1 queued events go undelivered"]


async def test_a_dead_consumer_with_nothing_queued_is_not_reported_as_lost_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty queue has no undelivered tail; reporting one at every clean shutdown is how a
    real one stops being believed."""
    caplog.set_level(logging.ERROR, logger="agentdeck.runtime.dispatch")
    sink = SelfCancelOnce()
    dispatch = SinkDispatch(sink)
    await dispatch.submit(_event(0))
    for _ in range(3):
        await asyncio.sleep(0)  # the consumer dies on seq 0 and leaves nothing behind it

    await dispatch.close()

    assert _errors(caplog) == []
    assert dispatch.depth == 0
    assert dispatch.dropped == 1  # the event that was in flight is still an event nobody got


async def test_close_finishes_against_a_sink_whose_emit_never_returns() -> None:
    """The hang this fix exists for: every exit condition in a shutdown that waits for the queue
    to empty is defeated by one emit that never comes back."""
    sink = Gated()  # never released
    dispatch = SinkDispatch(sink)
    before = len(asyncio.all_tasks())
    for seq in range(3):
        await dispatch.submit(_event(seq))

    async with asyncio.timeout(5):  # the hang, not a measurement: close's own bound is 0.2s
        await dispatch.close(timeout=0.2)

    assert sink.seqs() == []
    assert dispatch.dropped == 3  # the one wedged inside emit and the two behind it
    assert len(asyncio.all_tasks()) == before  # the consumer was cancelled, not left running


async def test_an_emit_that_never_returns_is_a_failure_and_trips_the_breaker() -> None:
    """A wedged sink is as broken as a raising one, and has to reach the breaker the same way —
    otherwise it is retried, one full timeout at a time, for the rest of the process."""
    sink = Gated()  # never released: every emit hits the timeout
    dispatch = SinkDispatch(sink, capacity=8, failure_limit=2, emit_timeout=0.01)
    before = len(asyncio.all_tasks())
    async with asyncio.timeout(10):
        for seq in range(3):
            await dispatch.submit(_event(seq))

        assert sink.seqs() == []  # the submits never waited on the wedged sink
        assert dispatch.depth <= 8
        assert len(asyncio.all_tasks()) == before + 1  # one consumer, whatever the sink is doing

        await dispatch.close()

    assert dispatch.failed == 2  # two emits timed out, back to back
    assert dispatch.disabled is True
    assert dispatch.dropped == 1  # the third never reached a sink already known to be broken
    assert dispatch.depth == 0


async def test_the_cancellation_close_sends_is_not_counted_as_the_sink_misbehaving() -> None:
    """The converse of a leaked ``CancelledError``: this one is ours, so the consumer ends and
    the event is a loss, not a failed emit."""
    sink = Gated()  # never released
    dispatch = SinkDispatch(sink)
    await dispatch.submit(_event(0))

    async with asyncio.timeout(5):
        await dispatch.close(timeout=0.05)

    assert dispatch.failed == 0
    assert dispatch.dropped == 1


async def test_a_cancelled_error_leaked_by_a_sink_is_a_failure_not_a_lost_event() -> None:
    """``emit`` raising ``CancelledError`` with nobody cancelling anything is a sink bug. The
    event was neither dropped nor failed before, which is the one thing this module promises
    never happens."""
    sink = CancelOnce()
    dispatch = SinkDispatch(sink, failure_limit=2)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.submit(_event(1))
        await dispatch.close()

    assert sink.raised is True
    assert dispatch.failed == 1  # only a caught CancelledError can be counted
    assert dispatch.dropped == 0
    assert sink.seqs() == [1]  # the consumer survived it and took the next event


async def test_a_flushed_dispatch_is_still_usable() -> None:
    """``flush`` is the mid-life wait, not the funeral: it exists so a caller can make sure a
    sink has caught up without giving up the sink."""
    sink = Recorder()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.flush()
        assert sink.seqs() == [0]

        await dispatch.submit(_event(1))
        await dispatch.close()

    assert sink.seqs() == [0, 1]
    assert (dispatch.dropped, dispatch.failed) == (0, 0)


async def test_an_event_submitted_after_close_is_counted_and_starts_no_consumer() -> None:
    """Closing is terminal: a run still winding down must not resurrect a consumer whose queue
    has already been written off, and its events are losses like any other."""
    sink = Recorder()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.close()

    before = len(asyncio.all_tasks())
    await dispatch.submit(_event(1))

    assert sink.seqs() == [0]
    assert dispatch.dropped == 1
    assert len(asyncio.all_tasks()) == before  # nothing was started to read a queue nobody owns


async def test_closing_twice_counts_nothing_twice_and_raises_no_second_alarm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shutdown paths overlap in real processes. The second close must be quiet — and must not
    write off the same stranded backlog again, which would inflate the loss it is reporting."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    untouched = SinkDispatch(Recorder())
    dispatch = SinkDispatch(Gated())  # never released, so the first close strands its backlog
    async with asyncio.timeout(5):
        for seq in range(3):
            await dispatch.submit(_event(seq))
        await dispatch.close(timeout=0.05)
        after_first = (dispatch.dropped, len(caplog.records))

        await dispatch.close(timeout=0.05)
        await untouched.close()
        await untouched.close()

    assert dispatch.dropped == 3
    assert (dispatch.dropped, len(caplog.records)) == after_first
    assert _errors(caplog) == []


def test_a_dispatch_that_could_not_bound_anything_is_rejected() -> None:
    """Every one of these is a bound that would silently stop being one."""
    sink = Recorder()
    with pytest.raises(ValueError, match="capacity"):
        SinkDispatch(sink, capacity=0)  # asyncio.Queue(0) is unbounded — the opposite of the point
    with pytest.raises(ValueError, match="capacity"):
        SinkDispatch(sink, capacity=-1)
    with pytest.raises(ValueError, match="failure_limit"):
        SinkDispatch(sink, failure_limit=0)
    with pytest.raises(ValueError, match="emit_timeout"):
        SinkDispatch(sink, emit_timeout=0)
    with pytest.raises(ValueError, match="emit_timeout"):
        SinkDispatch(sink, emit_timeout=-0.5)
