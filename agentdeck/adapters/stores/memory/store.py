"""The event log in a dict: the default for dev, tests and the contract suite."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, RunStatus, can_resume, status_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event


class MemoryEventStore(EventStorePort):
    """Append-only lists, one per (tenant, log key). Process exit is data loss, by design.

    Keyed by tenant as well as log key so two tenants that pick the same session id cannot
    read each other's runs — isolation is not something a store gets to skip.
    """

    def __init__(self) -> None:
        self._logs: dict[tuple[str, str], list[Event]] = {}

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        # The bucket is chosen by ctx, so an event stamped for another tenant would land in
        # the wrong one. The Runtime stamps from ctx and cannot diverge — this makes the
        # isolation the docstring claims enforced rather than merely true today.
        foreign = {event.tenant for event in events} - {ctx.tenant}
        if foreign:
            raise ValueError(f"events for tenant(s) {sorted(foreign)} cannot be written to {ctx.tenant!r}'s log")
        self._logs.setdefault((ctx.tenant, log_key), []).extend(events)
        # Fidelity, not correctness (issue #87): every real store suspends here (SQLite's own
        # `to_thread`), so a caller whose liveness secretly depends on that turn is caught by
        # this store too, instead of only by measurement in production. Placed after the
        # mutation above, so it opens no window in `claim_resume`'s atomicity.
        await asyncio.sleep(0)

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        log = self._logs.get((ctx.tenant, log_key), ())
        page = log[max(offset, 0) :]
        return list(page if limit is None else page[:limit])

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        log = self._logs.get((ctx.tenant, log_key), ())
        return [event for event in log if event.run_id == run_id and event.seq >= from_seq]

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        log = self._logs.get((ctx.tenant, log_key), ())
        return max((event.seq for event in log if event.run_id == run_id), default=-1)

    async def claim_start(self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime) -> SessionClaim:
        """Atomic for free, like ``claim_resume``: the scan and the append are plain dict work
        with no suspension point between them, so no other task can open a run in the gap."""
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")
        overridden: list[str] = []
        for run_id, events in _by_run(self._logs.get((ctx.tenant, log_key), ())).items():
            status = status_of(events)
            if status is RunStatus.PENDING or status in TERMINAL_STATUSES:
                continue
            if events[-1].ts > stale_before:
                return SessionClaim(held_by=run_id)
            overridden.append(run_id)
        await self.append(log_key, [event], ctx)
        return SessionClaim(overridden=tuple(overridden))

    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """Atomic for free: both checks and the write inside ``append`` are plain dict work
        with no suspension point between them, so no other task can slip in and claim the
        same run — ``append``'s own yield comes after that write, too late to open a window.
        """
        if event.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot carry an event for {event.run_id!r}")
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")
        mine = [stored for stored in self._logs.get((ctx.tenant, log_key), ()) if stored.run_id == run_id]
        if not can_resume(status_of(mine)):
            return False
        if event.seq != max((stored.seq for stored in mine), default=-1) + 1:
            return False
        await self.append(log_key, [event], ctx)
        return True

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        runs = [
            (log_key, event.run_id)
            for (tenant, log_key), log in self._logs.items()
            if tenant == ctx.tenant
            for event in log
            if event.kind in LIFECYCLE_KINDS
        ]
        # One fold per run through the port's own projection rather than a second inline
        # copy of it: re-walking a dev-sized dict is cheaper than two ways to derive status.
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=await self.run_status(log_key, run_id, ctx))
            for log_key, run_id in dict.fromkeys(runs)
        ]
        return [summary for summary in summaries if status is None or summary.status is status]


def _by_run(log: Sequence[Event]) -> dict[str, list[Event]]:
    """One log's events split per run, each list still in append order."""
    runs: dict[str, list[Event]] = {}
    for event in log:
        runs.setdefault(event.run_id, []).append(event)
    return runs


__all__ = ["MemoryEventStore"]
