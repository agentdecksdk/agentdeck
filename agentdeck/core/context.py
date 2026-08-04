"""What a run carries with it: who asked, which run, and the limits it was admitted under.

Passed explicitly to every port instead of read from ambient state — that is what makes
tenancy, tracing and budgets testable, and it is why an engine can never invent a tenant.
Frozen: a run's identity cannot change mid-flight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from agentdeck.core.events import Budget


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's identity and limits.

    ``tenant`` and ``principal`` are required and never defaulted downstream: they come
    from the caller at the edge or the run should not start.
    """

    tenant: str
    principal: str
    run_id: str
    trace_id: str
    session_id: str | None = None
    parent_run_id: str | None = None
    deadline: datetime | None = None
    budget: Budget | None = None
    idempotency_key: str | None = None
    triggered_by: str | None = None

    @property
    def log_key(self) -> str:
        """Where this run's events are written — a run without a session is its own log,
        so persist-before-yield holds for it too."""
        return self.session_id or self.run_id


__all__ = ["RunContext"]
