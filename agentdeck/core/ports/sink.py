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
    """One consumer of the stream. Errors and slowness are the sink's problem, not the run's."""

    @abstractmethod
    async def emit(self, event: Event) -> None:
        """Take one event.

        Called one event at a time per sink instance, in submission order, from a bounded
        buffer — so a slow ``emit`` costs this sink's own backlog and nothing else. A sink
        that cannot keep up sees gaps in that order rather than delaying the run: that is not
        negotiable, and a sink that needs every event reads the store, which is the ordered,
        complete copy.
        """


__all__ = ["EventSinkPort"]
