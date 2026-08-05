"""Read-only taps on the event stream: telemetry, cost, audit.

Fire-and-forget by contract — the Runtime never waits on a sink and never fails a run
because one failed. A sink that needs delivery guarantees reads the store instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from agentdeck.core.events import Event


class SinkFullPolicy(StrEnum):
    """What a sink wants done with an event it has no room for yet.

    ``DROP_OLDEST`` keeps the run moving and loses the stalest event — right for a
    telemetry or cost tap, where a gap is cheaper than a stalled run. ``BLOCK`` makes the
    producer wait for room instead, the only choice for a sink whose events must all
    arrive, and the reason it is never the default: the run pays for the wait.
    """

    DROP_OLDEST = "drop_oldest"
    BLOCK = "block"


class EventSinkPort(ABC):
    """One consumer of the stream. Errors and slowness are the sink's problem, not the run's."""

    on_full: ClassVar[SinkFullPolicy] = SinkFullPolicy.DROP_OLDEST
    """Set ``BLOCK`` on a subclass that must not miss an event, and accept the backpressure."""

    @abstractmethod
    async def emit(self, event: Event) -> None:
        """Take one event.

        Called one event at a time per sink instance, in ``seq`` order, from a buffer the
        run never waits on — so a slow ``emit`` costs this sink's backlog and nothing else.
        Under ``DROP_OLDEST`` a sink that cannot keep up sees gaps in that order rather than
        a delayed run; a sink that needs every event declares ``BLOCK`` or reads the store,
        which is the complete copy.
        """


__all__ = ["EventSinkPort", "SinkFullPolicy"]
