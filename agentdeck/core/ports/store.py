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

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event


@dataclass(frozen=True, slots=True)
class RunSummary:
    """One run's identity and derived status, as :meth:`EventStorePort.list_runs` projects
    it — never a stored row of its own (ADR-D5: the log stays the sole source of truth)."""

    log_key: str
    run_id: str
    status: RunStatus


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


__all__ = ["EventStorePort", "RunSummary"]
