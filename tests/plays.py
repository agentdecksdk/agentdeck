"""The log an executor sees for each of the three plays, for a test that calls one directly.

``Executor.execute`` reads the play off ``history`` (``core/status.continuation_of``), so a test
with no Runtime under it has to hand over the same two events a Runtime's claim would have
written. Built here rather than per test, because a hand-rolled tail that is subtly wrong reads
as a passing test of the wrong play.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import as_answer
from agentdeck.core.events import Event, RunInterrupted, RunPaused, RunResumed

if TYPE_CHECKING:
    from agentdeck.core.events import KnownPayload

_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _event(payload: KnownPayload, seq: int, run_id: str, session_id: str | None, origin: str) -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id=session_id,
        namespace="acme",
        origin=origin,
        ts=_TS,
        payload=payload,
    )


def answered(
    interrupt: RunInterrupted,
    value: Any,
    *,
    run_id: str,
    session_id: str | None = None,
    origin: str = "Subject",
) -> list[Event]:
    """A log whose tail says this run's interrupt was just answered with ``value``."""
    return [
        _event(interrupt, 0, run_id, session_id, origin),
        _event(RunResumed(value=as_answer(value)), 1, run_id, session_id, origin),
    ]


def lifted(*, run_id: str, session_id: str | None = None, origin: str = "Subject") -> list[Event]:
    """A log whose tail says this run's pause was just lifted."""
    return [
        _event(RunPaused(reason="operator stepped away"), 0, run_id, session_id, origin),
        _event(RunResumed(), 1, run_id, session_id, origin),
    ]


__all__ = ["answered", "lifted"]
