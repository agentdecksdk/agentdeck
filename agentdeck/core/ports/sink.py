"""Read-only taps on the event stream: telemetry, cost, audit.

Fire-and-forget by contract — the Runtime never waits on a sink and never fails a run
because one failed. A sink that needs delivery guarantees reads the store instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentdeck.core.events import Event


class EventSinkPort(ABC):
    """One consumer of the stream. Errors and slowness are the sink's problem, not the run's.

    A sink that blocks or raises too often is disabled, then offered one event again after a
    cooldown — so an outage needs no retry logic here. Nothing is replayed: the events the
    outage covered are lost, and a sink that cannot lose any reads the store.
    """

    @abstractmethod
    async def emit(self, event: Event) -> None:
        """Take one event, promptly.

        Called one at a time per sink, in submission order, from a bounded buffer — so a slow
        ``emit`` costs this sink's own backlog and nothing else. Slow work buffers internally
        and flushes in :meth:`close`; a sink that cannot keep up sees gaps in that order rather
        than delaying the run.
        """

    async def close(self) -> None:  # noqa: B027 — no-op on purpose: a stateless sink has nothing to flush
        """The stream has ended: write out whatever is still buffered. No-op by default.

        Called once at shutdown, and on a sink that never saw an event, and on one that was
        disabled — buffered events are still worth writing. Bounded and non-fatal: too slow is
        abandoned mid-flush, anything raised is logged.

        One ``emit`` may still be unwinding while this runs (a swallowed cancellation, or an
        ``await`` in its ``finally``), so a buffer must be safe to touch from two places:
        read-``await``-clear here can drop what that ``emit`` adds in between.
        """


__all__ = ["EventSinkPort"]
