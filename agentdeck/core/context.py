"""What a run carries with it: which run it is, what it continues, and where it is kept apart.

Passed explicitly to every port instead of read from ambient state — that is what makes
isolation and tracing testable, and it is why an engine can never invent a namespace.
Frozen: a run's identity cannot change mid-flight.

Deliberately holds no application identity. AgentDeck runs agents; it does not model users,
organizations or permissions, so nothing here says who is acting or what they may do. An
application that has those concepts keeps them, and may project one of them onto
``namespace`` — which AgentDeck then treats as an opaque key it never interprets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentdeck.core.control import Gate
from agentdeck.core.reporting import Reporter

if TYPE_CHECKING:
    from datetime import datetime

    from agentdeck.core.events import Budget


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's identity and limits.

    ``namespace`` is an opaque isolation boundary and nothing more. AgentDeck never parses it,
    never compares its parts, and attaches no meaning to it — an application may key it by
    workspace, project, business or anything else, and ``None`` (a single-namespace
    deployment) is a first-class mode, not a placeholder. It is deliberately not an identity:
    it says which runs are kept apart, never who is acting or what they may do. Authentication,
    authorization and ownership belong to the application above.

    Empty is rejected rather than accepted, because stores encode ``None`` as the empty key —
    so an explicit ``""`` would silently land in the same bucket as no namespace at all.

    ``gate`` and ``reporter`` are the two fields that are not values — a cooperative seam has to
    reach code the Runtime never sees. Both default to doing nothing and only the Runtime rebinds
    them, so a context built by hand is still a plain value object.
    """

    run_id: str
    trace_id: str
    session_id: str | None = None
    namespace: str | None = None
    parent_run_id: str | None = None
    deadline: datetime | None = None
    budget: Budget | None = None
    idempotency_key: str | None = None
    triggered_by: str | None = None
    gate: Gate = field(default_factory=Gate)
    reporter: Reporter = field(default_factory=Reporter)

    def __post_init__(self) -> None:
        if self.namespace is not None and not self.namespace:
            raise ValueError(
                "namespace must be a non-empty string or None; empty is how stores encode "
                "'no namespace', so an explicit '' would share a bucket with unnamespaced runs"
            )

    @property
    def log_key(self) -> str:
        """Where this run's events are written — a run without a session is its own log,
        so persist-before-yield holds for it too."""
        return self.session_id or self.run_id

    @property
    def namespace_key(self) -> str:
        """The namespace as a store keys by it: ``None`` is the empty key.

        One encoding, defined once, because four stores that each decided for themselves what
        "no namespace" looks like would be four chances to put the same run in two buckets.
        ``__post_init__`` refusing an explicit ``""`` is what lets the empty key mean exactly
        one thing.
        """
        return self.namespace or ""
