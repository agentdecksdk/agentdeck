"""How code inside a run says what it is doing: one report channel, carried on the context.

The mirror image of :class:`~agentdeck.core.control.Gate`  -  control in on ``RunContext``,
updates out the same way. A tool six frames inside an engine cannot yield an event and must not
know a Runtime exists, so it hands the report to the context it has.

Buffered, not delivered: the Runtime drains a bounded buffer at its next event, so an emitter is
never charged for a store append and a refused write never surfaces as an exception inside
somebody's tool. What it costs is timeliness, stated on :class:`Reporter`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from agentdeck.core.events import Reported

if TYPE_CHECKING:
    from collections import deque

    from agentdeck.core.base import JsonData
    from agentdeck.core.events import KnownPayload

logger = logging.getLogger(__name__)

# Deep enough for an honest burst: a run reporting more than this between two events of its own
# is describing itself faster than it is doing anything. Bounded because an invocable's own code
# fills it, which the platform does not get to trust with memory.
MAX_PENDING_REPORTS = 64


class Reporter:
    """One run's out-of-band report channel: three levels of prose, plus named records.

    Always synchronous  -  every method only enqueues into a thread-safe buffer, whether called
    from the event loop or a worker thread. Delivery out of the buffer may cross threads or a
    network; that never reaches this API. With no buffer  -  the default  -  each method still
    validates and then drops the result, so an emitter learns its numbers are nonsense even
    outside a wired run.
    """

    # ponytail: drained between payloads, not concurrently with them  -  a report emitted during one
    # long tool call surfaces when that call ends, and one emitted after the engine's last payload
    # is dropped. Lift it by racing the engine's stream against this buffer in its own task, when a
    # consumer needs the label *during* a single call rather than merely between calls.

    __slots__ = ("_lock", "_pending")

    def __init__(self, pending: deque[KnownPayload] | None = None) -> None:
        self._pending = pending
        self._lock = threading.Lock()

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

    def _offer(self, payload: KnownPayload) -> None:
        if self._pending is None:
            return
        with self._lock:
            if len(self._pending) >= MAX_PENDING_REPORTS:
                # Newest dropped, not oldest: a sequence missing its front looks like it started at 40.
                logger.warning(
                    "dropping %s: %d reports are already waiting to be recorded", payload.kind, len(self._pending)
                )
                return
            self._pending.append(payload)
