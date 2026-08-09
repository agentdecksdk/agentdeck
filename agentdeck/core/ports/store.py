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
    from datetime import timedelta

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload, RunResumed, RunStarted


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

    ``overridden`` is the **last event of** every run a winning claim stepped over as abandoned —
    not merely its id. The store wrote nothing for those: closing a run means deciding it is
    abandoned, which is judgement, and the store only reports what it saw. It saw these events
    already, having compared each one's ``ts`` to decide the run was stale, so handing them back
    costs nothing and saves the caller a read it would otherwise need to reconstruct the closing
    event's envelope (ADR-D11 §5).
    """

    held_by: str | None = None
    overridden: tuple[Event, ...] = ()


class EventStorePort(ABC):
    """Append-only. A log holds every run of one session, so it is ordered by *append*, not
    by ``seq`` — ``seq`` restarts at 0 for each run and only orders events within it.

    ``log_key`` is the session the events belong to, or the run itself when there is no
    session (``RunContext.log_key``) — a store never has to decide where to put them.

    **The store assigns ``seq`` and ``ts``** (ADR-D11), in the same indivisible step that
    persists the event. Callers hand over payloads and get finished events back. That is what
    makes ``seq`` dense: a number allocated and persisted together cannot be allocated and not
    persisted, so a gap means an event was genuinely lost and refetching it converges.

    Every other envelope field comes from ``ctx`` — ``run_id``, ``session_id``, ``namespace`` — plus
    ``origin``, which is the invocable the caller addressed. A store never decides what an event
    *means*; it refuses what would corrupt the log and reports what it saw.
    """

    @abstractmethod
    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        """Stamp and write ``payloads`` in the order given, returning the finished events.

        Each gets this run's next ``seq`` and the store's own ``ts``, assigned inside whatever
        the backend uses to make the write indivisible. Must return only once they are durable,
        because the Runtime yields to consumers immediately after.

        Run identity comes from ``ctx`` alone. The one write that belongs to a *different* run —
        the terminal event a takeover stamps for a run it stepped over — passes a ``ctx`` built
        for that run, from the event :class:`SessionClaim` handed back. There is no override
        parameter, because a caller that could address any run could file an event under a run it
        is not playing.
        """

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
    async def claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        """Stamp and append ``opening`` — a run's ``run.started`` — if and only if this log has no
        open run, in one indivisible step. One session runs one turn at a time. The event is
        returned when the claim won, ``None`` when it lost.

        Condition and write must be one operation, which is what carries this across processes:
        two servers sharing a store would both read an idle session and both open a run on it.

        An **open run** recorded a lifecycle transition and no terminal one. ``WAITING_HUMAN``
        counts — an interrupted run still owns its engine's thread, and a second run against it
        would overwrite the checkpoints the first resumes from. A run with no transition at all is
        ``PENDING``, indistinguishable from one the store never saw, so it holds nothing.

        Losing never raises — two turns at once is a double-clicked send button, so the refusal is
        data. An unreachable store does raise: it cannot know whether anybody holds anything.

        ``stale_after`` is how long an open run may be silent before it stops holding the session:
        one whose last event is older than that comes back in ``overridden`` for the caller to
        close. A duration rather than a cutoff instant, because the caller no longer owns the clock
        the comparison is made in — the store does, and only it can subtract from its own now.
        Without this a process killed mid-run wedges its session for good.
        """

    @abstractmethod
    async def claim_resume(
        self, log_key: str, run_id: str, resumed: RunResumed, ctx: RunContext, origin: str
    ) -> Event | None:
        """Stamp and append ``resumed`` if and only if ``run_id`` is ``WAITING_HUMAN``, in one
        indivisible step. The event when appended, ``None`` when not.

        The write publishing the ``WAITING_HUMAN`` -> ``RUNNING`` transition is the same write
        that tests for it, which is what makes double-resume protection hold between processes
        and not merely between tasks. A store that cannot do both indivisibly must not implement
        this port. A concurrent loser reads ``RUNNING`` — the winner's append is what flipped it —
        and gets ``None``.

        Status is the whole condition now. It used to be paired with a stale-``seq`` check, which
        existed because the caller stamped before claiming; the caller no longer holds a ``seq`` to
        go stale. Note what that check did *not* cover and still does not: a resume can answer an
        interrupt other than the one in flight, because nothing here names which interrupt is being
        answered. Recording that is #94's business, in a schema PR.

        ``None`` on any other status or a lost race; a stray resume is a no-op by design
        (``can_resume``). An unreachable store raises instead, because ``None`` claims somebody
        else recorded this resume, which it cannot know.
        """

    @abstractmethod
    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Every run in this namespace that recorded a lifecycle transition, across all its logs,
        optionally narrowed to one status.

        ``PENDING`` runs are left out, being indistinguishable from ones the store never heard of.
        Index this however the store can: finding waiting runs must not cost a fold of every log
        the namespace owns.
        """

    async def run_status(self, log_key: str, run_id: str, ctx: RunContext) -> RunStatus:
        """One run's status, derived from its own events only — never the whole log.

        Default projection: fold :meth:`read_run` through ``status_of`` (ADR-D5: a projection,
        not a second store). A store with a cheaper way to answer may override it.
        """
        return status_of(await self.read_run(log_key, run_id, ctx))
