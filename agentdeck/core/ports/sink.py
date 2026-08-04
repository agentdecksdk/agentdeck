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

        Called concurrently and in no guaranteed order: the Runtime dispatches each event
        without waiting for the last, so a sink that awaits anything can be re-entered on
        the same instance and can see ``seq`` 3 before ``seq`` 2. A sink that needs order
        sorts by ``seq``, or reads the store instead — the log is the ordered copy.
        """


__all__ = ["EventSinkPort"]
