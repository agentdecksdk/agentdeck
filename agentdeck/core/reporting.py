"""How code inside a run says what it is doing: one report channel, carried on the context.

The mirror image of :class:`~agentdeck.core.control.Gate` — control in on ``RunContext``,
updates out the same way. A tool six frames inside an engine cannot yield an event and must not
know a Runtime exists, so it hands the report to the context it has.

Buffered, not delivered: the Runtime drains a bounded buffer at its next event, so an emitter is
never charged for a store append and a refused write never surfaces as an exception inside
somebody's tool. What it costs is timeliness, stated on :class:`Reporter`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentdeck.core.events import ProgressReported, StatusReported

if TYPE_CHECKING:
    from collections import deque

    from agentdeck.core.events import KnownPayload

logger = logging.getLogger(__name__)

# Deep enough for an honest burst: a run reporting more than this between two events of its own
# is describing itself faster than it is doing anything. Bounded because an invocable's own code
# fills it, which the platform does not get to trust with memory.
MAX_PENDING_REPORTS = 64


class Reporter:
    """One run's out-of-band report channel: ``status`` in prose, ``progress`` in stages.

    With no buffer — the default — both methods still validate and then drop the result, so an
    emitter learns its numbers are nonsense even outside a wired run.

    The Runtime binds a real one per run and drains it *before* each event the engine yields,
    keeping reports in log order and ahead of the terminal event.
    """

    # ponytail: drained between payloads, not concurrently with them — a report emitted during one
    # long tool call surfaces when that call ends, and one emitted after the engine's last payload
    # is dropped. Lift it by racing the engine's stream against this buffer in its own task, when a
    # consumer needs the label *during* a single call rather than merely between calls.

    __slots__ = ("_pending",)

    def __init__(self, pending: deque[KnownPayload] | None = None) -> None:
        self._pending = pending

    async def status(self, message: str) -> None:
        """Report what the run is doing now, for a person to read. ``message`` must not be empty.

        Async so a channel that one day waits — a queue, a transport — need not change its
        callers. Nothing here awaits today.
        """
        self._offer(StatusReported(message=message))

    async def progress(self, step: str, *, current: int | None = None, total: int | None = None) -> None:
        """Report a named stage, optionally counted. Raises if ``current`` is past ``total``."""
        self._offer(ProgressReported(step=step, current=current, total=total))

    def _offer(self, payload: KnownPayload) -> None:
        if self._pending is None:
            return
        if len(self._pending) >= MAX_PENDING_REPORTS:
            # Newest dropped, not oldest: a progress sequence missing its front is a run that
            # appears to start at step 40, worse than one that stops reporting. Logged rather
            # than raised — an advisory event is not worth a run.
            logger.warning(
                "dropping %s: %d reports are already waiting to be recorded", payload.kind, len(self._pending)
            )
            return
        self._pending.append(payload)
