"""The event log — the platform's record of what happened.

Not the engine's execution state: an engine that needs its exact prior items keeps them
privately (ADR-D5). This log is what replay, audit and every surface read from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentdeck.core.status import RunStatus, status_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One run as :meth:`EventStorePort.list_runs` projects it — never a stored row of its
    own (ADR-D5: the log stays the sole source of truth)."""

    log_key: str
    run_id: str
    status: RunStatus


@dataclass(frozen=True, slots=True)
class SessionClaim:
    """What one :meth:`EventStorePort.claim_start` decided.

    ``held_by`` names the open run that refused the claim, ``None`` when the claim won.
    ``overridden`` is every run a winning claim stepped over as abandoned; the store wrote
    nothing for those, since closing a run means stamping an event and only the Runtime does
    that.
    """

    held_by: str | None = None
    overridden: tuple[str, ...] = ()


class EventStorePort(ABC):
    """Append-only. A log holds every run of one session, so it is ordered by *append*, not
    by ``seq`` — ``seq`` restarts at 0 for each run and only orders events within it.

    ``log_key`` is the session the events belong to, or the run itself when there is no
    session (``RunContext.log_key``) — a store never has to decide where to put them.
    """

    @abstractmethod
    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        """Write events in the order given. Must return only once they are durable, because
        the Runtime yields to consumers immediately after."""

    @abstractmethod
    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        """Events in append order, oldest first.

        ``offset`` counts from the start of the log, not a ``seq`` cursor — ``seq`` restarts per
        run, so it cannot address a position in a log holding several. Safe to page with a plain
        counter: the log only grows at the end.

        A negative ``offset`` means 0; a negative ``limit`` raises ``ValueError`` rather than
        quietly meaning "all" in one store and "none" in another.
        """

    @abstractmethod
    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        """One run's events from ``from_seq`` onward, inclusive — what a consumer calls to
        refetch after spotting a gap.

        A range read has to name the run: a seq range over a whole log would splice together
        the tails of every run in it.
        """

    @abstractmethod
    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        """The highest ``seq`` recorded for this run, or -1 if it has none — what the Runtime
        recovers its per-run counter from on resume."""

    @abstractmethod
    async def claim_start(self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime) -> SessionClaim:
        """Append ``event`` — a run's opening ``run.started`` — if and only if this log has no
        open run, in one indivisible step. One session runs one turn at a time.

        Condition and write must be one operation, which is what carries this across processes:
        two servers sharing a store would both read an idle session and both open a run on it.

        An **open run** recorded a lifecycle transition and no terminal one. ``WAITING_HUMAN``
        counts — an interrupted run still owns its engine's thread, and a second run against it
        would overwrite the checkpoints the first resumes from. A run with no transition at all is
        ``PENDING``, indistinguishable from one the store never saw, so it holds nothing.

        Losing never raises — two turns at once is a double-clicked send button, so the refusal is
        data. An unreachable store does raise: it cannot know whether anybody holds anything.

        ``stale_before`` is the cutoff for an abandoned claim: an open run whose last event is at
        or before it stops holding the session and comes back in ``overridden`` for the caller to
        close. Without it a process killed mid-run wedges its session for good.
        """

    @abstractmethod
    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """Append ``event`` if and only if ``run_id`` is ``WAITING_HUMAN`` *and* ``event.seq``
        is the next ``seq`` for that run, in one indivisible step. ``True`` when appended.

        The write publishing the ``WAITING_HUMAN`` -> ``RUNNING`` transition is the same write
        that tests for it, which is what makes double-resume protection hold between processes
        and not merely between tasks. A store that cannot do both indivisibly must not implement
        this port.

        The ``seq`` check covers what status alone cannot: a caller stamps its event before
        claiming, so a slow one can arrive after the run was resumed *and* interrupted again —
        waiting on a human once more, but with that ``seq`` already spent.

        ``False`` on any other status, a stale ``seq``, or a lost race; a stray resume is a no-op
        by design (``can_resume``). An unreachable store raises instead, because ``False`` claims
        somebody else recorded this resume, which it cannot know.
        """

    @abstractmethod
    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Every run for this tenant that recorded a lifecycle transition, across all its logs,
        optionally narrowed to one status.

        ``PENDING`` runs are left out, being indistinguishable from ones the store never heard of.
        Index this however the store can: finding waiting runs must not cost a fold of every log
        the tenant owns.
        """

    async def run_status(self, log_key: str, run_id: str, ctx: RunContext) -> RunStatus:
        """One run's status, derived from its own events only — never the whole log.

        Default projection: fold :meth:`read_run` through ``status_of`` (ADR-D5: a projection,
        not a second store). A store with a cheaper way to answer may override it.
        """
        return status_of(await self.read_run(log_key, run_id, ctx))
