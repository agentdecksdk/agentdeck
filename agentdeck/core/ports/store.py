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
    """One run's identity and derived status, as :meth:`EventStorePort.list_runs` projects
    it — never a stored row of its own (ADR-D5: the log stays the sole source of truth)."""

    log_key: str
    run_id: str
    status: RunStatus


@dataclass(frozen=True, slots=True)
class SessionClaim:
    """What one :meth:`EventStorePort.claim_start` decided.

    ``held_by`` names the open run that refused the claim, and is ``None`` when the claim won.
    ``overridden`` is every run a winning claim stepped over because nobody was coming back
    for it: the store wrote nothing for those, since closing a run means stamping an event and
    only the Runtime does that.
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
        """Events in append order, oldest first. ``offset`` is a count of events to skip from
        the start of the log, not a ``seq`` cursor — ``seq`` restarts per run, so it cannot
        address a position in a log that holds several. ``limit`` caps how many come back,
        ``None`` for the rest.

        Safe to page with a plain counter: the log only ever grows at the end, so an earlier
        page never shifts under a later read. A negative ``offset`` means the same as 0; a
        negative ``limit`` is a caller bug and raises ``ValueError`` rather than quietly
        meaning "all" in one store and "none" in another.
        """

    @abstractmethod
    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        """One run's events from ``from_seq`` onward, inclusive.

        A range read has to name the run: ``seq`` is per run, so a seq range over a whole
        log would splice together the tails of every run in it. This is what a consumer
        calls to refetch after spotting a gap.
        """

    @abstractmethod
    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        """The highest ``seq`` recorded for this run, or -1 if it has none yet.

        What the Runtime recovers its per-run counter from on resume, instead of reading
        every event to fold the same max by hand.
        """

    @abstractmethod
    async def claim_start(self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime) -> SessionClaim:
        """Append ``event`` — a run's opening ``run.started`` — if and only if, at that moment,
        this log has no open run, in one indivisible step. One session runs one turn at a time.

        An **open run** is one that recorded a lifecycle transition and has not recorded a
        terminal one. ``WAITING_HUMAN`` counts: an interrupted run still owns its engine's
        thread, and a second run against that thread would write over the checkpoints the
        first one resumes from. A run with no transition at all is ``PENDING``, which no store
        can tell from a run it never saw, so it holds nothing — the same line
        :meth:`list_runs` draws.

        Losing is not an error and never raises: two turns arriving together is a
        double-clicked send button, not a broken store, so the refusal is data —
        ``SessionClaim.held_by`` names the run that has the session and nothing is written. A
        store nobody can reach still raises, for the same reason it does in
        :meth:`claim_resume`: it cannot know whether anybody holds anything.

        Making the condition and the write one operation is what carries this across
        processes: two servers sharing a store would both read an idle session and both open a
        run on it, whereas only one ``run.started`` can land here.

        ``stale_before`` is the cutoff for an abandoned claim. An open run whose own last event
        is at or before it stops holding the session and comes back in
        ``SessionClaim.overridden`` for the caller to close — without it a process killed
        mid-run would wedge its session for good. A store never reads a clock: the caller
        stamps ``ts``, so the caller is the only one whose idea of "now" can be compared to it.
        """

    @abstractmethod
    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """Append ``event`` if and only if, at that moment, ``run_id`` is ``WAITING_HUMAN``
        *and* ``event.seq`` is the next ``seq`` for that run — one indivisible step. ``True``
        when it was appended, ``False`` on any other status or a stale ``seq``, including
        when another caller got there first.

        The one write that publishes the ``WAITING_HUMAN`` -> ``RUNNING`` transition is the
        same write that tests for it, which is what makes double-resume protection hold
        between two processes sharing a store and not merely between two tasks. Losing is
        never an error: a stray resume is a no-op by design (``can_resume``), so a store
        returns ``False`` rather than raising. A store it cannot reach at all is a different
        answer and does raise — ``StoreError`` from the durable ones — because ``False`` says
        somebody else already recorded this resume, which an unreachable store cannot know.

        The ``seq`` condition covers what the status alone cannot. A caller stamps its event
        before claiming, so a slow one can arrive after the run was resumed *and* interrupted
        again: waiting on a human once more, but with that ``seq`` already spent. Refusing it
        there is what keeps duplicate ``seq``s out of a log — a store that cannot make both
        checks and the append indivisible must not implement this port.
        """

    @abstractmethod
    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Every run for this tenant that has recorded a lifecycle transition, across all of
        the tenant's logs, optionally narrowed to one status.

        A run with no transition yet is ``PENDING``, which is indistinguishable from a run
        this store has never heard of — so a listing cannot meaningfully report it, and both
        stores leave it out. Every run the Runtime starts records ``run.started`` first.

        A store is free to enumerate however it can index — the point of this query is that
        finding waiting runs must not cost a fold of every log the tenant owns.
        """

    async def run_status(self, log_key: str, run_id: str, ctx: RunContext) -> RunStatus:
        """One run's status, derived from its own events only — never the whole log.

        Default projection: fold this run's events through ``status_of`` (ADR-D5: a
        projection, not a second store), fetched by :meth:`read_run`, which every store
        already indexes by run. A store with a cheaper way to answer this may override it.
        """
        return status_of(await self.read_run(log_key, run_id, ctx))


__all__ = ["EventStorePort", "RunSummary", "SessionClaim"]
