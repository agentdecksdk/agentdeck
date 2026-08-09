"""Durable timer waits (issue #22): ``sleep_until`` parks a node until a wall-clock
moment, built entirely on ``interrupt()`` (see ``workflows.interrupts``). A node calling
``interrupt()`` re-runs **from its start** on resume, so everything before it executes
twice: keep the calling node pure, side effects belong in earlier nodes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langgraph.types import interrupt

TIMER_TYPE = "agentdeck.timer"
WAKE_AT_KEY = "wake_at"


def _require_aware(when: datetime) -> datetime:
    if when.tzinfo is None:
        raise ValueError(f"sleep_until requires a timezone-aware datetime; got naive {when!r}.")
    return when


def sleep_until(when: datetime) -> Any:
    """Pause the calling node until ``when`` (timezone-aware; naive datetimes are rejected).

    Wraps ``langgraph.types.interrupt()`` with a payload convention —
    ``{"type": "agentdeck.timer", "wake_at": <ISO-8601 UTC>}`` — so a paused-on-timer thread is
    distinguishable from a paused-on-human thread in a pending-runs listing (``Deck.pending()``).
    A due-timer sweep resumes with the wake timestamp as the resume value.
    Requires ``durable = True``, same as any other ``interrupt()`` call.
    """
    return interrupt({"type": TIMER_TYPE, WAKE_AT_KEY: _require_aware(when).astimezone(UTC).isoformat()})


def wake_at_of(payload: Any) -> datetime | None:
    """The wake time encoded by a ``sleep_until`` payload, or ``None`` if it isn't one."""
    if not isinstance(payload, dict) or payload.get("type") != TIMER_TYPE:
        return None
    wake_at = payload.get(WAKE_AT_KEY)
    return datetime.fromisoformat(wake_at) if isinstance(wake_at, str) else None


__all__ = ["TIMER_TYPE", "WAKE_AT_KEY", "sleep_until", "wake_at_of"]
