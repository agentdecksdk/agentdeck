"""How code inside a run says what it is doing: one report channel, carried on the context.

The mirror image of :class:`~agentdeck.core.control.Gate`  -  control in on ``RunContext``,
updates out the same way. A tool six frames inside an engine cannot yield an event and must not
know a Runtime exists, so it hands the report to the context it has.

Written when made, never awaited: the Runtime binds a writer that schedules the append, so no
emitter waits on a store and a refused write never surfaces inside somebody's tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.events import Reported

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentdeck.core.base import JsonData


class Reporter:
    """One run's out-of-band report channel: three levels of prose, plus named records.

    Always synchronous  -  every method hands the payload to ``write`` and returns, whether called
    from the event loop or a worker thread. Persisting it may cross threads or a network; that
    never reaches this API. With no writer  -  the default  -  each method still validates and then
    drops the result, so an emitter learns its numbers are nonsense even outside a wired run.
    """

    # ponytail: a consumer reading the Runtime's generator still sees a report at the engine's next
    # payload. Lift it by racing that stream against a report queue in its own task (#487 item 2).

    __slots__ = ("_write",)

    def __init__(self, write: Callable[[Reported], None] | None = None) -> None:
        self._write = write

    def info(self, message: str, **fields: JsonData) -> None:
        """Report what the run is doing now, for a person to read. ``message`` must not be empty."""
        self._offer(Reported(level="info", message=message, fields=fields))

    def warning(self, message: str, **fields: JsonData) -> None:
        """Report something the run worked around: a fallback taken, a source unavailable."""
        self._offer(Reported(level="warning", message=message, fields=fields))

    def error(self, message: str, **fields: JsonData) -> None:
        """Report something the run could not do. Advisory either way: reporting an error is not
        failing the run, which is what raising does."""
        self._offer(Reported(level="error", message=message, fields=fields))

    def report(self, name: str, **fields: JsonData) -> None:
        """Record a named, structured fact  -  ``report("candidate_found", score=0.91)``  -  for a
        consumer that filters rather than reads. The name is the record's message, so a reader
        that knows nothing about it still has something to show."""
        self._offer(Reported(level="record", message=name, fields=fields))

    def _offer(self, payload: Reported) -> None:
        if self._write is not None:
            self._write(payload)
