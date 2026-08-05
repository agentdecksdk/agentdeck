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
    """Only a run waiting on a human answer can be resumed with a value. A signal against
    any other status — including a race that already resumed it — is a no-op, not an
    error: the caller checks this instead of raising."""
    return status is RunStatus.WAITING_HUMAN


__all__ = ["LIFECYCLE_KINDS", "TERMINAL_STATUSES", "RunStatus", "can_resume", "status_of"]
