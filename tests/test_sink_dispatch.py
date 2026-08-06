"""What one sink's bounded queue does when the sink cannot keep up: what it keeps, what it
drops, and when it gives up on the sink entirely. What it never does is wait for it.

Every assertion is on counts and queue depth rather than on elapsed time — a bound that only
shows up as "fast enough" is not a bound.
"""

from __future__ import annotations

import asyncio
import contextlib
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


class CancelDeaf(Recorder):
    """Swallows the cancellation ``close`` sends, the way an over-broad ``except`` in ``emit`` does.

    Never takes an event, and never lets go of the consumer that is inside it — the one sink that
    a cancel alone cannot get shutdown past.
    """

    def __init__(self) -> None:
        super().__init__()
        self.wedged = asyncio.Event()

    async def emit(self, event: Event) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await self.wedged.wait()  # never set: a cancellation is the only way out, and it eats it


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


class Buffering(EventSinkPort):
    """Holds every event and writes the lot out only when closed.

    The shape the emit contract pushes a real sink into: ``emit`` does in-memory work, and the
    buffer reaches wherever it is going on a schedule of the sink's own — of which shutdown is
    the last one. ``written`` is what actually left; ``buffered`` is what would be lost.
    """

    def __init__(self) -> None:
        self.buffered: list[Event] = []
        self.written: list[int] = []
        self.closes = 0
        self.emits_after_close = 0

    async def emit(self, event: Event) -> None:
        if self.closes:
            # Recorded rather than raised: a raise here would be caught by the dispatch and
            # counted as one more failed emit, which is not what went wrong.
            self.emits_after_close += 1
        self.buffered.append(event)

    async def close(self) -> None:
        self.closes += 1
        self.written.extend(event.seq for event in self.buffered)
        self.buffered.clear()


class FailsAfterOne(Buffering):
    """Buffers its first event and fails every one after it.

    The awkward case the breaker creates: by the time it is disabled it is still holding an
    event that a flush would get out.
    """

    async def emit(self, event: Event) -> None:
        if self.buffered or self.written:
            raise RuntimeError("collector is down")
        await super().emit(event)


class CloseWedged(Buffering):
    """Never returns from ``close``, the way a final flush to a dead endpoint does not."""

    async def close(self) -> None:
        self.closes += 1
        await asyncio.Event().wait()  # nobody holds it: only the deadline ends this


class CloseBroken(Buffering):
    """Raises out of ``close`` — a shutdown is not the place to find out about it the hard way."""

    async def close(self) -> None:
        self.closes += 1
        raise RuntimeError("collector rejected the flush")


class Stubborn(Buffering):
    """Keeps working inside ``emit`` after swallowing a cancellation, instead of dying of one.

    Not malicious and not wedged — a finite emit that outlives the reap's deadline, which is the
    one shape that leaves a consumer alive *inside* ``emit`` when the deadline fires. ``CloseDeaf``
    swallows its first cancel and then returns, so the next one lands in ``queue.get()`` and kills
    it; this one is still in the sink when the reap gives up on it.
    """

    def __init__(self, slices: int = 5) -> None:
        super().__init__()
        self._slices = slices

    async def emit(self, event: Event) -> None:
        for _ in range(self._slices):
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(0.02)
        await super().emit(event)


class CloseCancels(Buffering):
    """Leaks a ``CancelledError`` out of ``close``, the way a timeout scope of its own does.

    Nobody cancelled anything, so a shutdown that took it for a real cancellation would abandon
    the rest of its work over a sink's bug.
    """

    async def close(self) -> None:
        self.closes += 1
        raise asyncio.CancelledError


class CloseDeaf(Buffering):
    """Swallows the cancellation the reap sends, then carries on taking events.

    The one way an event can still reach a sink after its ``close``: its consumer outlived the
    cancel meant to retire it, so cancelling is not what makes the promise hold.
    """

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def emit(self, event: Event) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await self.release.wait()  # never set: the reap's cancel is the only way out
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

    await dispatch.close()  # the flush above left a live consumer behind; nothing else retires it


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
        await dispatch.close(timeout=5)  # under the guard, so a regression fails an assert, not a race

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


async def test_a_consumer_that_ate_its_cancellation_still_retires_at_its_next_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sink that eats the cancellation is not an exit condition on its own, so the consumer has a
    second way out that does not depend on one landing: once the dispatch is closed, its own loop
    ends. Only a consumer still *inside* such an ``emit`` when the deadline passes is abandoned.
    """
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    monkeypatch.setattr("agentdeck.runtime.dispatch.REAP_TIMEOUT", 0.05)  # the bound, shrunk
    sink = CancelDeaf()
    dispatch = SinkDispatch(sink)
    before = len(asyncio.all_tasks())
    await dispatch.submit(_event(0))

    async with asyncio.timeout(5):  # the hang this bound exists for, not a measurement
        await dispatch.close(timeout=0.05)

    assert sink.seqs() == []
    assert len(asyncio.all_tasks()) == before  # ended, and by its own loop rather than the cancel
    assert [record.getMessage() for record in caplog.records if "did not release" in record.getMessage()] == []


async def test_close_does_not_swallow_a_cancellation_aimed_at_its_caller() -> None:
    """The reap's own deadline must not become a cancellation shield: a caller cancelled mid-close
    and handed a clean return carries on as if it had never been asked to stop.

    Needs a sink whose consumer is still inside ``emit`` when the caller's deadline lands, since
    that is the only state in which the reap is still waiting on anything.
    """
    sink = Stubborn(slices=20)  # 0.4s of cancellation-proof work, against a 0.2s caller deadline
    dispatch = SinkDispatch(sink)
    before = len(asyncio.all_tasks())
    await dispatch.submit(_event(0))

    with pytest.raises(TimeoutError):  # the caller's deadline, delivered as a cancel into close
        async with asyncio.timeout(0.2):
            await dispatch.close(timeout=0.1)

    await asyncio.sleep(0.5)
    assert len(asyncio.all_tasks()) == before  # the abandoned consumer retires itself, not leaks


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


async def test_a_buffering_sink_writes_its_buffer_out_when_the_dispatch_closes() -> None:
    """Draining the queue is only half a shutdown: a sink that answers every emit in memory has
    delivered nothing until something tells it the stream is over."""
    sink = Buffering()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):  # a hang guard, not a measurement
        for seq in range(5):
            await dispatch.submit(_event(seq))
        await dispatch.flush()

        assert (sink.written, [event.seq for event in sink.buffered]) == ([], [0, 1, 2, 3, 4])
        await dispatch.close()

    assert sink.written == [0, 1, 2, 3, 4]
    assert (sink.closes, sink.emits_after_close) == (1, 0)
    assert (dispatch.dropped, dispatch.failed, dispatch.close_failed) == (0, 0, False)


async def test_a_sink_that_defines_no_close_is_closed_without_one() -> None:
    """The hook is optional: every sink written before it existed goes on working untouched, which
    is what the port's default is for."""
    sink = Recorder()
    assert type(sink).close is EventSinkPort.close  # nothing in its MRO overrides it

    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.close()

    assert (sink.seqs(), dispatch.close_failed) == ([0], False)


async def test_a_closed_sink_is_never_handed_another_event(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """What a sink may assume, and the case that makes it worth stating: a consumer that ate the
    cancellation retiring it is still there, still reading, and must not reach a sink that has
    already let go of its buffer."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    monkeypatch.setattr("agentdeck.runtime.dispatch.REAP_TIMEOUT", 0.05)
    sink = CloseDeaf()
    dispatch = SinkDispatch(sink)
    await dispatch.submit(_event(0))  # the consumer wedges inside this one
    await dispatch.submit(_event(1))  # what it would take next, if anything still could

    async with asyncio.timeout(5):  # the hang this bound exists for, not a measurement
        await dispatch.close(timeout=0.05)

    assert sink.emits_after_close == 0
    assert (sink.closes, sink.written) == (1, [0])  # the event it did take still got written out
    assert dispatch.dropped == 1  # seq 1 reached a consumer that was no longer allowed to emit


async def test_a_sink_that_hangs_in_its_close_does_not_hold_shutdown_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The hook is a wait on a sink like any other, so it gets a deadline like any other —
    a flush to a dead endpoint must cost a shutdown a few seconds, not the process."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    monkeypatch.setattr("agentdeck.runtime.dispatch.CLOSE_TIMEOUT", 0.05)  # the bound, shrunk
    sink = CloseWedged()
    dispatch = SinkDispatch(sink)
    before = len(asyncio.all_tasks())
    await dispatch.submit(_event(0))

    async with asyncio.timeout(5):  # the hang this bound exists for, not a measurement
        await dispatch.close()

    assert (sink.closes, dispatch.close_failed) == (1, True)
    assert "sink CloseWedged did not finish its close within 0.05s; whatever it still held is lost" in [
        record.getMessage() for record in caplog.records
    ]
    assert len(asyncio.all_tasks()) == before  # the abandoned flush is not left running either


async def test_a_sink_that_raises_in_its_close_never_breaks_the_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed flush is lost telemetry; a shutdown that raises out of ``drain`` is a lost
    process. The event log holds the record either way."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    sink = CloseBroken()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.close()

    assert dispatch.close_failed is True
    assert dispatch.failed == 0  # its emits were fine; only the flush was not
    tracebacks = [record for record in caplog.records if record.exc_info is not None]
    assert len(tracebacks) == 1
    assert "failed in its close" in tracebacks[0].getMessage()


async def test_a_consumer_still_inside_emit_when_the_reap_gives_up_does_not_hold_shutdown_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The reap's deadline cannot be a deadline *around* ``await consumer``: one fires by cancelling
    the waiting task, which forwards it straight into the sink that just swallowed the last one —
    spending the deadline on the sink instead of ending the wait, with nothing left to fire again.

    Then the close hook is never reached at all, which is the shape of hang this asserts against:
    the sink's flush is behind this wait.
    """
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    monkeypatch.setattr("agentdeck.runtime.dispatch.REAP_TIMEOUT", 0.01)
    sink = Stubborn(slices=20)  # 0.4s of cancellation-proof work, against a 0.01s reap deadline
    dispatch = SinkDispatch(sink)
    before = len(asyncio.all_tasks())
    await dispatch.submit(_event(0))
    for _ in range(3):
        await asyncio.sleep(0)  # the consumer gets inside emit, where the cancel will find it

    # No ``asyncio.timeout`` guard here, unlike every other test in this file: a guard fires by
    # cancelling this task, whose innermost await would be the very wait under test, so it would be
    # forwarded into the sink and swallowed too. The state below is the assertion instead.
    await dispatch.close(timeout=0.01)

    # Closed while that emit was still running: the sink had buffered nothing yet, so an empty
    # ``written`` is proof the flush did not sit behind the 0.4s of work — and ``closes`` is proof
    # it was reached at all, which is what the unbounded wait prevented outright.
    assert (sink.closes, sink.written) == (1, [])
    assert "sink Stubborn did not release its consumer within 0.01s; abandoning the task" in [
        record.getMessage() for record in caplog.records
    ]
    await asyncio.sleep(0.5)
    assert len(asyncio.all_tasks()) == before  # and the consumer it walked away from retires itself


async def test_a_backlog_written_off_by_close_is_not_counted_again_by_a_surviving_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer abandoned mid-``emit`` keeps running, and draining on would count events the
    close already reported as lost — inflating the only number an operator has for how much a
    shutdown cost. It retires at its next turn instead."""
    monkeypatch.setattr("agentdeck.runtime.dispatch.REAP_TIMEOUT", 0.01)
    sink = Stubborn()
    dispatch = SinkDispatch(sink, capacity=4)
    async with asyncio.timeout(5):
        for seq in range(3):
            await dispatch.submit(_event(seq))
        await dispatch.close(timeout=0.01)

        assert dispatch.dropped == 3  # the one in flight and the two behind it
        await asyncio.sleep(0.3)  # long enough for the abandoned consumer to finish its emit

    assert dispatch.dropped == 3  # and it took no further event, so nothing is counted twice
    # The one caveat the port documents, and the reason it does: the emit already in flight
    # finished after the close, so this sink appended to a buffer its own close had emptied.
    assert (sink.emits_after_close, sink.written) == (1, [])
    assert [event.seq for event in sink.buffered] == [0]  # seq 0 only: 1 and 2 were never handed over


async def test_a_cancelled_error_leaked_by_a_sink_s_close_is_counted_like_any_other_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same sink bug ``emit`` already guards against, one method along: a bare
    ``CancelledError`` with nobody cancelling anything must not read as a real cancellation."""
    caplog.set_level(logging.WARNING, logger="agentdeck.runtime.dispatch")
    sink = CloseCancels()
    dispatch = SinkDispatch(sink)
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.close()

    assert (sink.closes, dispatch.close_failed, dispatch.failed) == (1, True, 0)
    assert any("CancelledError from its close" in record.getMessage() for record in caplog.records)


async def test_the_close_hook_does_not_swallow_a_cancellation_aimed_at_its_caller() -> None:
    """The hook's own deadline must not become a cancellation shield: a caller cancelled while a
    sink is still flushing and handed a clean return carries on as if it had never asked to stop."""
    dispatch = SinkDispatch(CloseWedged())

    with pytest.raises(TimeoutError):  # the caller's deadline, delivered as a cancel into close
        async with asyncio.timeout(0.1):
            await dispatch.close()


async def test_a_sink_is_closed_once_however_many_times_shutdown_reaches_it() -> None:
    """Shutdown paths overlap in real processes, and a second close would hand a sink that has
    already written its buffer out a second flush of nothing."""
    sink = Buffering()
    dispatch = SinkDispatch(sink)
    untouched = Buffering()  # never submitted to: a sink with no consumer is still closed
    async with asyncio.timeout(10):
        await dispatch.submit(_event(0))
        await dispatch.close()
        await dispatch.close()
        await SinkDispatch(untouched).close()

    assert (sink.closes, sink.written) == (1, [0])
    assert (untouched.closes, untouched.written) == (1, [])


async def test_a_disabled_sink_is_still_closed_so_what_it_buffered_before_survives() -> None:
    """The breaker's verdict is about taking events, not about writing out the ones already
    taken — a sink disabled halfway through a run is exactly the one holding a buffer nobody
    else can flush."""
    sink = FailsAfterOne()
    dispatch = SinkDispatch(sink, failure_limit=2)
    async with asyncio.timeout(10):
        for seq in range(5):
            await dispatch.submit(_event(seq))
        await dispatch.close()

    assert dispatch.disabled is True
    assert (sink.closes, sink.written) == (1, [0])  # the one event it took reached its endpoint
    assert (sink.emits_after_close, dispatch.close_failed) == (0, False)
    assert (dispatch.failed, dispatch.dropped) == (2, 2)


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
