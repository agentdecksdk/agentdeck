"""Run status: derived from the event log's own transitions, never a second store.

There is no status *table* — the run-lifecycle events already are the transitions, so
"persist status" and "persist the log" are the same write. A reader recovers status by
folding a run's events in order, which is what makes it correct after a restart: whatever
the log says is whatever status is, with no cache to go stale.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.events import Event


class RunStatus(StrEnum):
    """A run's lifecycle. ``PAUSED`` and ``WAITING_HUMAN`` resume differently: the first
    with nothing, the second with a value — callers must not conflate them."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_HUMAN = "waiting_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})

# Suspended, not finished: a run in one of these has an engine to re-enter and a terminal
# event still owed. Cancelled is deliberately absent — terminal is terminal.
RESUMABLE_STATUSES = frozenset({RunStatus.WAITING_HUMAN, RunStatus.PAUSED})

# Only these kinds move the needle; everything else (deltas, tool calls, node.updated, ...)
# leaves status exactly where it was.
_KIND_TO_STATUS: dict[str, RunStatus] = {
    "run.started": RunStatus.RUNNING,
    "run.paused": RunStatus.PAUSED,
    "run.resumed": RunStatus.RUNNING,
    "run.interrupted": RunStatus.WAITING_HUMAN,
    "run.completed": RunStatus.COMPLETED,
    "run.failed": RunStatus.FAILED,
    "run.cancelled": RunStatus.CANCELLED,
}

# A store that can index by kind only has to look at these to answer "what status is this
# run" — the derivation itself stays here, in ``status_of``.
LIFECYCLE_KINDS: frozenset[str] = frozenset(_KIND_TO_STATUS)


def status_of(events: Sequence[Event]) -> RunStatus:
    """One run's status: the last transition kind wins, in log order. No events at all is
    ``PENDING`` — a run whose ``run.started`` hasn't even been read yet."""
    status = RunStatus.PENDING
    for event in events:
        status = _KIND_TO_STATUS.get(event.kind, status)
    return status


def can_resume(status: RunStatus) -> bool:
    """A run is resumable while it is suspended: waiting on a human answer, or paused by an
    operator. Both continue under the same ``run_id`` and the same append that flips them back
    to ``RUNNING``; what differs is what the resume carries (a value, or nothing).

    A resume against any other status — a run still running, a race that already resumed it,
    anything terminal — is a no-op, not an error: the caller checks this instead of raising."""
    return status in RESUMABLE_STATUSES


__all__ = [
    "LIFECYCLE_KINDS",
    "RESUMABLE_STATUSES",
    "TERMINAL_STATUSES",
    "RunStatus",
    "can_resume",
    "status_of",
]
