"""Bounded hand-off from a run to one event sink.

One queue and one consumer task per sink, instead of one task per event: a wedged sink
then costs a fixed backlog and a single task, whatever the run does next. What cannot fit
is dropped — never waited for, because a run is never charged for its slowest reader — but
always counted and logged, because a tap that quietly stops taking events is
indistinguishable from one that never had any.

Guaranteed delivery to a sink is a deliberate non-goal: a consumer that must not miss an
event reads the event store, which is the ordered, complete copy. If a sink ever genuinely
needs every event, that is a new behavior layered on this one — not a reason to make a run
wait here.
"""

from __future__ import annotations

import asyncio
import contextlib
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


class SinkDispatch:
    """One sink's bounded queue, the consumer task that empties it, and what it lost.

    Not shared between sinks: each has its own queue, so a stalled sink's backlog can
    neither displace another sink's events nor slow them down.
    """

    def __init__(
        self,
        sink: EventSinkPort,
        *,
        capacity: int = QUEUE_CAPACITY,
        failure_limit: int = FAILURE_LIMIT,
    ) -> None:
        self._sink = sink
        self._name = type(sink).__name__
        self._queue: asyncio.Queue[Event] = asyncio.Queue(capacity)
        self._failure_limit = failure_limit
        self._consumer: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self.dropped = 0
        self.failed = 0
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
        if self.disabled:
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

    async def drain(self) -> None:
        """Wait for the queued events to reach the sink, then stop the consumer.

        Shutdown only: waiting per event is the exact join the queue exists to avoid. A
        later ``submit`` starts a fresh consumer, so draining twice is safe. The wait is
        raced against the consumer itself, because a queue whose consumer has died can never
        empty — and this is called for every sink at once, so one dead consumer waited on
        unconditionally would cost every other sink its tail.
        """
        consumer = self._consumer
        if consumer is None:
            return
        flushed = asyncio.create_task(self._queue.join())
        await asyncio.wait({flushed, consumer}, return_when=asyncio.FIRST_COMPLETED)
        flushed.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flushed
        if consumer.done():
            logger.error("sink %s has no consumer left; %d queued events go undelivered", self._name, self.depth)
        self._consumer = None
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        if self.dropped or self.failed:
            logger.warning("sink %s lost %d events and failed %d emits", self._name, self.dropped, self.failed)

    def _ensure_consumer(self) -> None:
        # Started on first use, not in __init__: a Runtime is usually built before the loop runs.
        # Restarted when it is gone: a CancelledError escaping a sink's own emit (a leaked
        # timeout scope, a cancel arriving mid-shutdown) kills the consumer without raising
        # anything here, and a queue nobody reads swallows every event after that.
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """Take the queue one event at a time, for as long as the sink is worth calling.

        A disabled sink's queue is still emptied rather than abandoned, so the backlog it
        leaves behind is counted as lost instead of just quietly sitting there.
        """
        while True:
            event = await self._queue.get()
            try:
                if self.disabled:
                    self._count_drop(event)
                else:
                    await self._emit(event)
            finally:
                self._queue.task_done()

    async def _emit(self, event: Event) -> None:
        try:
            await self._sink.emit(event)
        except Exception:
            self.failed += 1
            self._consecutive_failures += 1
            if self._consecutive_failures == 1:
                # One traceback per streak: a sink failing on every event of a long run would
                # otherwise bury the log in a thousand copies of the same stack.
                logger.exception("sink %s failed on %s seq=%d", self._name, event.kind, event.seq)
            elif self.failed % LOG_INTERVAL == 0:
                logger.warning(
                    "sink %s has failed %d emits, %d of them in a row",
                    self._name,
                    self.failed,
                    self._consecutive_failures,
                )
            if self._consecutive_failures >= self._failure_limit:
                self.disabled = True
                logger.error(
                    "sink %s disabled after %d consecutive failures; its events are dropped from here on",
                    self._name,
                    self._consecutive_failures,
                )
        else:
            self._consecutive_failures = 0

    def _count_drop(self, event: Event) -> None:
        self.dropped += 1
        if self.dropped == 1 or self.dropped % LOG_INTERVAL == 0:
            logger.warning(
                "sink %s dropped %s seq=%d (%d events lost so far)", self._name, event.kind, event.seq, self.dropped
            )


__all__ = ["FAILURE_LIMIT", "LOG_INTERVAL", "QUEUE_CAPACITY", "SinkDispatch"]
