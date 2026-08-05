"""Bounded hand-off from a run to one event sink.

One queue and one consumer task per sink, instead of one task per event: a wedged sink
then costs a fixed backlog and a single task, whatever the run does next. What cannot fit
is dropped or waited for — the sink's own choice — but always counted and logged, because
a tap that quietly stops taking events is indistinguishable from one that never had any.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from agentdeck.core.ports import SinkFullPolicy

if TYPE_CHECKING:
    from agentdeck.core.events import Event
    from agentdeck.core.ports import EventSinkPort

logger = logging.getLogger(__name__)

# Deep enough to absorb a burst from a chatty run, small enough that a wedged sink's
# backlog stays a rounding error against the log it is a copy of.
QUEUE_CAPACITY = 256

# A sink that fails this many times in a row is broken, not unlucky.
FAILURE_LIMIT = 5

# Every drop is counted; one in this many is also logged, so a wedged sink stays visible
# without turning one bad tap into a log flood of its own.
DROP_LOG_INTERVAL = 100


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

        Returns without suspending under ``DROP_OLDEST``: a full queue loses its stalest
        event rather than making the caller wait, which is what keeps a run off its slowest
        sink. ``BLOCK`` is the deliberate opposite — the caller waits for room, because for
        that sink a missing event is worse than a slow run.
        """
        if self.disabled:
            self._count_drop(event)
            return
        self._ensure_consumer()
        if self._sink.on_full is SinkFullPolicy.BLOCK:
            await self._queue.put(event)
            return
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
        later ``submit`` starts a fresh consumer, so draining twice is safe.
        """
        consumer = self._consumer
        if consumer is None:
            return
        await self._queue.join()
        self._consumer = None
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        if self.dropped or self.failed:
            logger.warning("sink %s lost %d events and failed %d emits", self._name, self.dropped, self.failed)

    def _ensure_consumer(self) -> None:
        # Started on first use, not in __init__: a Runtime is usually built before the loop runs.
        if self._consumer is None:
            self._consumer = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """Take the queue one event at a time, for as long as the sink is worth calling.

        A disabled sink still has its queue emptied — a ``BLOCK`` producer may be waiting
        for room, and nothing is ever going to make room for it again.
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
            logger.exception("sink %s failed on %s seq=%d", self._name, event.kind, event.seq)
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
        if self.dropped == 1 or self.dropped % DROP_LOG_INTERVAL == 0:
            logger.warning(
                "sink %s dropped %s seq=%d (%d events lost so far)", self._name, event.kind, event.seq, self.dropped
            )


__all__ = ["DROP_LOG_INTERVAL", "FAILURE_LIMIT", "QUEUE_CAPACITY", "SinkDispatch"]
