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

    An ``emit`` must therefore return promptly — a sink whose work is slow buffers internally
    and flushes on its own schedule, because the dispatch feeding it times out a blocking emit
    and eventually disables a sink that keeps doing it. Since that pushes every non-trivial
    sink into buffering, ``close`` is where a buffer gets written out for the last time.
    """

    @abstractmethod
    async def emit(self, event: Event) -> None:
        """Take one event.

        Called one event at a time per sink instance, in submission order, from a bounded
        buffer — so a slow ``emit`` costs this sink's own backlog and nothing else. A sink
        that cannot keep up sees gaps in that order rather than delaying the run: that is not
        negotiable, and a sink that needs every event reads the store, which is the ordered,
        complete copy.
        """

    async def close(self) -> None:  # noqa: B027 — no-op on purpose: a stateless sink has nothing to flush
        """The stream has ended: write out whatever is still buffered. Does nothing by default.

        Called once at shutdown, and only after the last ``emit`` has returned — so a sink may
        release what it was holding here without guarding the rest of itself against a further
        event. A sink whose failures got it disabled is closed too: the events it buffered
        before that are still worth writing out, and being bad at taking events says nothing
        about being able to flush the ones already taken.

        Bounded and non-fatal, like every other wait on a sink: one that takes too long is
        abandoned mid-flush, and anything raised here is logged and counted rather than
        allowed to break a shutdown. A sink that needs its flush to be certain is asking for
        delivery guarantees, which belong to the store, not here.
        """


__all__ = ["EventSinkPort"]
