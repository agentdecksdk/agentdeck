"""How code inside a run says what it is doing: one report channel, carried on the context.

The mirror image of :class:`~agentdeck.core.ports.control.Gate`. A cancel signal has to reach
code the Runtime never sees, so it travels *in* on ``RunContext``; a status update has the same
problem in the other direction — a tool six frames inside an engine cannot yield an event, and
must not know a Runtime exists. So it hands the report to the context it already has, and the
Runtime, which owns the stream, is the only thing that turns one into an event.

Buffered rather than delivered: a report is appended to a bounded buffer the Runtime drains at
its next event. That is what keeps an advisory update from ever failing a tool call — an emitter
is never charged for a store append, and a store that refuses one cannot surface as an exception
inside somebody's tool. The Runtime holds the other half of that bargain: a report the store
refuses is dropped and logged, never turned into a failed run. What it costs is timeliness,
stated on :class:`Reporter`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentdeck.core.events import ProgressReported, StatusReported

if TYPE_CHECKING:
    from collections import deque

    from agentdeck.core.events import KnownPayload

logger = logging.getLogger(__name__)

# Deep enough for any honest burst — a run reporting more than this between two events of its
# own is describing itself faster than it is doing anything — and bounded because the buffer is
# filled by an invocable's own code, which the platform does not get to trust with memory.
MAX_PENDING_REPORTS = 64


class Reporter:
    """One run's out-of-band report channel: ``status`` in prose, ``progress`` in stages.

    With no buffer — the default — both methods validate their arguments and drop the result,
    so a ``RunContext`` a caller built themselves behaves exactly as before, and an emitter
    still learns immediately that its numbers are nonsense rather than only in the run that
    happens to be wired.

    The Runtime binds a real one per run and drains it *before* each event the engine yields,
    which is what keeps reports in log order and always ahead of the terminal event. The ceiling
    that follows: a report emitted while the engine is producing nothing waits for the engine's
    next payload, so a status set inside one long tool call surfaces when that call ends rather
    than while it runs, and one emitted after the engine's last payload is dropped. Lifting it
    means racing the engine's stream against this buffer in a task of its own; the trigger is a
    consumer that needs the label *during* a single call, not merely between calls or nodes.
    """

    __slots__ = ("_pending",)

    def __init__(self, pending: deque[KnownPayload] | None = None) -> None:
        self._pending = pending

    async def status(self, message: str) -> None:
        """Report what the run is doing now, for a person to read. ``message`` must not be empty.

        Async because every other seam an invocable's code touches is, and because a channel that
        one day has to wait — for a real queue, a transport — must not change its callers when it
        does. Nothing here awaits today.
        """
        self._offer(StatusReported(message=message))

    async def progress(self, step: str, *, current: int | None = None, total: int | None = None) -> None:
        """Report a named stage, optionally counted. Raises if ``current`` is past ``total``."""
        self._offer(ProgressReported(step=step, current=current, total=total))

    def _offer(self, payload: KnownPayload) -> None:
        if self._pending is None:
            return
        if len(self._pending) >= MAX_PENDING_REPORTS:
            # The newest is dropped, not the oldest: a progress sequence read with its front
            # missing is a run that appears to start at step 40, which is worse than one that
            # stops reporting. Logged rather than raised — an advisory event is not worth a run.
            logger.warning(
                "dropping %s: %d reports are already waiting to be recorded", payload.kind, len(self._pending)
            )
            return
        self._pending.append(payload)


__all__ = ["MAX_PENDING_REPORTS", "Reporter"]
