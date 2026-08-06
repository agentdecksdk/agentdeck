"""Bounded hand-off from a run to one event sink.

One queue and one consumer task per sink, instead of one task per event: a wedged sink
then costs a fixed backlog and a single task, whatever the run does next. What cannot fit
is dropped — never waited for, because a run is never charged for its slowest reader — but
always counted and logged, because a tap that quietly stops taking events is
indistinguishable from one that never had any.

NFR-6 — slow sinks never stall a run — is the requirement behind that, because a run pinned
to its slowest reader turns every optional tap into a liveness risk for the run itself. Its
reach does not stop at the event path: waiting on a sink is a liveness risk wherever it is
done, so nothing here waits on one without a deadline — not even shutdown, which a single
emit that never returns would otherwise hold open forever.

Guaranteed delivery to a sink is a deliberate non-goal: a consumer that must not miss an
event reads the event store, which is the ordered, complete copy. If a sink ever genuinely
needs every event, that is a new behavior layered on this one — not a reason to make a run
wait here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentdeck.core.events import Event
    from agentdeck.core.ports import EventSinkPort

logger = logging.getLogger(__name__)

# Deep enough to absorb a burst from a chatty run, small enough that a wedged sink's
# backlog stays a rounding error against the log it is a copy of.
QUEUE_CAPACITY = 256

# A sink that fails this many times in a row is broken, not unlucky.
FAILURE_LIMIT = 5

# Every drop and every failure is counted; one in this many is also logged, so a bad sink
# stays visible without turning one bad tap into a log flood of its own.
LOG_INTERVAL = 100

# Generous for a slow HTTP round trip, finite for one that will never come back: an emit
# that outlives this is indistinguishable from a hung one, and waiting on it forever would
# leave the sink's whole backlog — and any shutdown behind it — hostage to a single event.
EMIT_TIMEOUT = 5.0

# The longest a shutdown waits for one sink's backlog before writing it off. Shutdown has
# to finish even against a sink that never returns, and the store already has every event.
SHUTDOWN_TIMEOUT = 10.0

# The longest a shutdown waits for a sink's own ``close`` — its last chance to write out what
# it buffered. A budget of its own rather than a share of the one above, because a sink whose
# backlog just timed out is exactly the one holding something worth flushing, and it would
# reach this with nothing left to spend.
CLOSE_TIMEOUT = 5.0

# The longest a shutdown then waits for the cancelled consumer task to actually end. Short
# because by this point nothing is expected of it: a sink whose emit swallows the
# cancellation keeps its consumer alive through it, and waiting unbounded on a task that has
# already been written off would put back the hang everything above removes.
REAP_TIMEOUT = 1.0


def _cancelling_ourselves() -> bool:
    """True when the ``CancelledError`` in hand is one this task was asked to honour.

    The difference matters at every point where one is caught: a cancel aimed at this task has
    to keep travelling, because swallowing it hands the caller a clean return from something it
    asked to stop, and the caller then carries on as if it had never asked.
    """
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


class SinkDispatch:
    """One sink's bounded queue, the consumer task that empties it, and what it lost.

    Not shared between sinks: each has its own queue, so a stalled sink's backlog can
    neither displace another sink's events nor slow them down. A sink the breaker disables
    stays disabled for this dispatch's lifetime — retrying belongs to the sink, which knows
    what it is talking to, while the dispatch only knows it stopped working.
    """

    def __init__(
        self,
        sink: EventSinkPort,
        *,
        capacity: int = QUEUE_CAPACITY,
        failure_limit: int = FAILURE_LIMIT,
        emit_timeout: float = EMIT_TIMEOUT,
    ) -> None:
        # Rejected rather than clamped: `asyncio.Queue(0)` is an unbounded queue, the exact
        # opposite of what a caller asking for zero capacity meant, and a bound that quietly
        # becomes "no bound" is worse than no bound at all.
        if capacity <= 0:
            raise ValueError(f"capacity must be greater than 0, got {capacity}")
        if failure_limit <= 0:
            raise ValueError(f"failure_limit must be greater than 0, got {failure_limit}")
        if emit_timeout <= 0:
            raise ValueError(f"emit_timeout must be greater than 0, got {emit_timeout}")
        self._sink = sink
        self._name = type(sink).__name__
        self._queue: asyncio.Queue[Event] = asyncio.Queue(capacity)
        self._failure_limit = failure_limit
        self._emit_timeout = emit_timeout
        self._consumer: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._inflight: Event | None = None
        self._closed = False
        self._quiesced = False
        self.dropped = 0
        self.failed = 0
        self.close_failed = False
        self.disabled = False

    @property
    def depth(self) -> int:
        """Events waiting for the sink — never more than the queue's capacity."""
        return self._queue.qsize()

    async def submit(self, event: Event) -> None:
        """Hand one event to the sink's queue.

        Yields at most one loop turn when the queue is full, and never waits for the sink:
        the turn is what tells a sink that is keeping up apart from one that is not, and only
        then is the stalest event dropped.
        """
        if self.disabled or self._closed:
            self._count_drop(event)
            return
        self._ensure_consumer()
        try:
            self._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            # A run can fill the queue without the loop ever turning — nothing on the event
            # path has to suspend — and then a sink is "full" because the producer is fast,
            # not because the sink is slow. One turn is room for a sink that is keeping up
            # and no help at all to a wedged one, which is exactly the distinction wanted.
            await asyncio.sleep(0)
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                self._count_drop(self._queue.get_nowait())
                self._queue.task_done()

    async def flush(self, timeout: float = SHUTDOWN_TIMEOUT) -> None:
        """Wait, bounded, for the queued events to be *attempted*, leaving the dispatch usable.

        Attempted, not delivered: the wait ends once the consumer has taken every queued event
        and returned from ``emit``, whether that emit succeeded, raised, or timed out — the
        queue's own join cannot tell those apart and this call does not try to.

        Never called per event: waiting per event is the exact join the queue exists to avoid.
        The wait is raced against the consumer, because a queue whose consumer has died can
        never empty — and this runs for every sink at once, so one dead consumer waited on
        unconditionally would cost every other sink its tail. It is bounded on top of that,
        because a sink wedged inside ``emit`` keeps its consumer alive and its queue full.
        """
        consumer = self._consumer
        if consumer is None:
            return
        flushed = asyncio.create_task(self._queue.join())
        done, _ = await asyncio.wait({flushed, consumer}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            # Neither finished, so the deadline is what ended the wait — a consumer that died
            # instead is the caller's story to tell, and a truer one than a timeout.
            logger.warning(
                "sink %s did not take its backlog within %ss; %d events still queued",
                self._name,
                timeout,
                self.depth,
            )
        flushed.cancel()
        try:
            await flushed
        except asyncio.CancelledError:
            if _cancelling_ourselves():
                raise

    async def close(self, timeout: float = SHUTDOWN_TIMEOUT) -> None:
        """Flush what the sink has not taken, stop its consumer, close the sink, count the rest.

        Returns in bounded time whatever the sink does: every wait it makes has a deadline, so a
        sink that neither takes an event, nor lets go of its consumer, nor finishes its own
        ``close`` can delay a shutdown but never prevent one.

        Terminal and idempotent: closing marks the dispatch shut before it waits for anything,
        so an event submitted by a run still winding down is counted as lost instead of landing
        in a queue nobody will read again — the window between "backlog flushed" and "consumer
        cancelled" is exactly where an event would otherwise be stranded or resurrect a
        consumer this call just retired. The sink's ``close`` happens exactly once, and after the
        consumer is reaped rather than beside it: cancelling the consumer first is what gets a
        sink out of an ``emit`` before it is asked to flush — for every sink that honours a
        cancellation, which is as far as a shutdown can go with the ones that do not.
        """
        if self._closed:
            return
        self._closed = True
        await self.flush(timeout)
        # Nothing reaches the sink's ``emit`` from here on, whichever way the flush ended: the
        # cancel below does not always land (a sink can swallow it and keep its consumer), and a
        # sink told its stream is over must not then be handed one more event.
        self._quiesced = True
        consumer = self._consumer
        self._consumer = None
        if consumer is not None:
            if consumer.done() and self.depth > 0:
                logger.error("sink %s has no consumer left; %d queued events go undelivered", self._name, self.depth)
            consumer.cancel()
            try:
                # Waited on from the outside, never as a deadline wrapped around ``await consumer``:
                # a deadline fires by cancelling *this* task, and a task suspended on another one
                # hands that cancel straight to it — into the very sink that just proved it
                # swallows them, leaving the deadline spent and nothing left to fire again.
                reaped, _ = await asyncio.wait({consumer}, timeout=REAP_TIMEOUT)
                if reaped:
                    # Done, so this cannot suspend and no cancel can be forwarded into it; awaiting
                    # is only how the task's outcome gets collected instead of logged by the loop.
                    await consumer
                else:
                    # A sink that swallows the cancellation inside its own emit keeps the consumer
                    # running, and shutdown is not the place to argue with it: whether the task dies
                    # later is the sink's business, not ours, and this call has to end either way.
                    logger.warning(
                        "sink %s did not release its consumer within %ss; abandoning the task",
                        self._name,
                        REAP_TIMEOUT,
                    )
            except asyncio.CancelledError:
                if _cancelling_ourselves():
                    raise
        await self._close_sink()
        # Cancelling the consumer strands its queue and whatever emit it was inside; both are
        # losses, and counters that disagree with the log line below are how a lost tail turns
        # into a mystery.
        self.dropped += self.depth + (1 if self._inflight is not None else 0)
        if self.dropped or self.failed:
            logger.warning("sink %s lost %d events and failed %d emits", self._name, self.dropped, self.failed)

    async def _close_sink(self) -> None:
        """Give the sink its one chance to write out what it buffered, bounded and non-fatal.

        A shutdown that a sink's flush could hang, or fail, would be a worse bargain than the
        lost telemetry it is trying to save — the event log is still the complete record either
        way, so everything that can go wrong here is counted and logged instead.
        """
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await self._sink.close()
        except TimeoutError:
            self.close_failed = True
            logger.warning(
                "sink %s did not finish its close within %ss; whatever it still held is lost",
                self._name,
                CLOSE_TIMEOUT,
            )
        except asyncio.CancelledError:
            if _cancelling_ourselves():
                raise
            self.close_failed = True
            logger.exception("sink %s raised CancelledError from its close", self._name)
        except Exception:
            self.close_failed = True
            logger.exception("sink %s failed in its close; whatever it still held is lost", self._name)

    def _ensure_consumer(self) -> None:
        # Started on first use, not in __init__: a Runtime is usually built before the loop runs.
        # Restarted when it is gone: a CancelledError escaping a sink's own emit (a leaked
        # timeout scope, a cancel arriving mid-shutdown) kills the consumer without raising
        # anything here, and a queue nobody reads swallows every event after that. Never reached
        # after a close, because ``submit`` refuses first — a consumer started here afterwards
        # would own a queue whose contents close already counted as lost.
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """Take the queue one event at a time, for as long as the sink is worth calling.

        A disabled sink's queue is still emptied rather than abandoned, so the backlog it
        leaves behind is counted as lost instead of just quietly sitting there. A close is the
        opposite case and ends the loop: a consumer that outlived the cancellation retiring it
        would otherwise drain a backlog the close already wrote off, counting every event in it
        a second time — and hand a closed sink events besides.
        """
        while not self._quiesced:
            event = await self._queue.get()
            self._inflight = event
            try:
                if self.disabled or self._quiesced:
                    self._count_drop(event)
                else:
                    await self._emit(event)
                # Cleared before ``task_done``, so an event is either accounted for here or
                # still on the books as in flight when a cancellation lands mid-emit.
                self._inflight = None
            finally:
                self._queue.task_done()

    async def _emit(self, event: Event) -> None:
        try:
            async with asyncio.timeout(self._emit_timeout):
                await self._sink.emit(event)
        except TimeoutError:
            self._count_failure(f"timed out after {self._emit_timeout}s on {event.kind} seq={event.seq}")
        except asyncio.CancelledError:
            if _cancelling_ourselves():
                # Ours (a close, a loop shutdown) — the consumer is meant to end here.
                raise
            self._count_failure(f"raised CancelledError from emit on {event.kind} seq={event.seq}")
        except Exception:
            self._count_failure(f"failed on {event.kind} seq={event.seq}")
        else:
            self._consecutive_failures = 0

    def _count_failure(self, reason: str) -> None:
        """Book one failed emit, however it failed: a raise, a timeout, a leaked cancellation.

        One place for all three, because the breaker's streak only means anything if every way
        a sink can not take an event feeds it — a sink hung inside ``emit`` is as broken as one
        raising on every call, and is disabled by the same count.
        """
        self.failed += 1
        self._consecutive_failures += 1
        if self._consecutive_failures == 1:
            # One traceback per streak: a sink failing on every event of a long run would
            # otherwise bury the log in a thousand copies of the same stack.
            logger.exception("sink %s %s", self._name, reason)
        elif self.failed % LOG_INTERVAL == 0:
            logger.warning(
                "sink %s has failed %d emits, %d of them in a row",
                self._name,
                self.failed,
                self._consecutive_failures,
            )
        if self._consecutive_failures >= self._failure_limit:
            # TODO(sagi): #89 — a cooldown and a half-open retry, if permanent is too blunt.
            self.disabled = True
            logger.error(
                "sink %s disabled after %d consecutive failures; its events are dropped from here on",
                self._name,
                self._consecutive_failures,
            )

    def _count_drop(self, event: Event) -> None:
        self.dropped += 1
        if self.dropped == 1 or self.dropped % LOG_INTERVAL == 0:
            logger.warning(
                "sink %s dropped %s seq=%d (%d events lost so far)", self._name, event.kind, event.seq, self.dropped
            )


__all__ = [
    "CLOSE_TIMEOUT",
    "EMIT_TIMEOUT",
    "FAILURE_LIMIT",
    "LOG_INTERVAL",
    "QUEUE_CAPACITY",
    "REAP_TIMEOUT",
    "SHUTDOWN_TIMEOUT",
    "SinkDispatch",
]
