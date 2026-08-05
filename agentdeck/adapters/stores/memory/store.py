"""The event log in a dict: the default for dev, tests and the contract suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.ports import EventStorePort, RunSummary
from agentdeck.core.status import status_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.core.status import RunStatus


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

    async def read(self, log_key: str, ctx: RunContext, after: int = 0, limit: int | None = None) -> list[Event]:
        log = self._logs.get((ctx.tenant, log_key), ())
        page = log[after:]
        return list(page if limit is None else page[:limit])

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        log = self._logs.get((ctx.tenant, log_key), ())
        return [event for event in log if event.run_id == run_id and event.seq >= from_seq]

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        log = self._logs.get((ctx.tenant, log_key), ())
        return max((event.seq for event in log if event.run_id == run_id), default=-1)

    async def list_log_keys(self, ctx: RunContext) -> list[str]:
        return [log_key for tenant, log_key in self._logs if tenant == ctx.tenant]

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        by_run: dict[tuple[str, str], list[Event]] = {}
        for (tenant, log_key), log in self._logs.items():
            if tenant != ctx.tenant:
                continue
            for event in log:
                by_run.setdefault((log_key, event.run_id), []).append(event)
        summaries = (
            RunSummary(log_key=lk, run_id=rid, status=status_of(events)) for (lk, rid), events in by_run.items()
        )
        return [summary for summary in summaries if status is None or summary.status is status]


__all__ = ["MemoryEventStore"]
