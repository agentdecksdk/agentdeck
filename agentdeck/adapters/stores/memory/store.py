"""The event log in a dict: the default for dev, tests and the contract suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentdeck.core.events import Event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, STATES, RunStatus, can_resume, status_of

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import timedelta

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload, RunResumed, RunStarted


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryEventStore(EventStorePort):
    """Append-only lists, one per (namespace, log key). Process exit is data loss, by design.

    Keyed by namespace as well as log key so two namespaces that pick the same session id cannot
    read each other's runs — isolation is not something a store gets to skip.
    """

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._logs: dict[tuple[str | None, str], list[Event]] = {}
        self._clock = clock

    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        events = self._stamp(log_key, payloads, ctx, origin)
        # Fidelity, not correctness (issue #87): every real store suspends here (SQLite's own
        # `to_thread`), so a caller whose liveness secretly depends on that turn is caught by
        # this store too, instead of only by measurement in production. Placed after the
        # mutation in `_stamp`, so it opens no window in either claim's atomicity.
        await asyncio.sleep(0)
        return events

    def _stamp(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        """Assign, build and write, with no suspension point anywhere in between.

        That is this store's whole atomicity mechanism, and it is why every caller here — both
        claims included — goes through this rather than through ``append``: an ``await`` between
        reading the run's last ``seq`` and extending the log is all it would take for two tasks to
        be handed the same number.
        """
        log = self._logs.setdefault((ctx.namespace, log_key), [])
        seq = max((stored.seq for stored in log if stored.run_id == ctx.run_id), default=-1)
        events = []
        for payload in payloads:
            seq += 1
            events.append(
                Event(
                    kind=payload.kind,
                    seq=seq,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    namespace=ctx.namespace,
                    origin=origin,
                    ts=self._clock(),
                    payload=payload,
                )
            )
        log.extend(events)
        return events

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        log = self._logs.get((ctx.namespace, log_key), ())
        page = log[max(offset, 0) :]
        return list(page if limit is None else page[:limit])

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        log = self._logs.get((ctx.namespace, log_key), ())
        return [event for event in log if event.run_id == run_id and event.seq >= from_seq]

    async def claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        """Atomic for free, like ``claim_resume``: the scan and ``_stamp`` are plain dict work
        with no suspension point between them, so no other task can open a run in the gap.
        """
        stale_before = self._clock() - stale_after
        overridden: list[Event] = []
        for events in _by_run(self._logs.get((ctx.namespace, log_key), ())).values():
            status = status_of(events)
            if status is None or STATES[status].terminal:
                continue
            if STATES[status].suspended:
                # No worker to be dead: PAUSED and WAITING_ANSWER have no engine polling a
                # clock, so silence is not evidence of anything and the timer does not apply.
                # The log deciding alone is what makes this session's hold permanent.
                return SessionClaim(held_by=events[-1].run_id), None
            if events[-1].ts > stale_before:
                return SessionClaim(held_by=events[-1].run_id), None
            overridden.append(events[-1])
        event = self._stamp(log_key, [opening], ctx, origin)[0]
        await asyncio.sleep(0)
        return SessionClaim(overridden=tuple(overridden)), event

    async def claim_resume(
        self, log_key: str, run_id: str, resumed: RunResumed, ctx: RunContext, origin: str
    ) -> Event | None:
        """Atomic for free: the status fold and ``_stamp`` are plain dict work with no suspension
        point between them, so no other task can slip in and claim the same run.
        """
        if ctx.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot be made in the context of {ctx.run_id!r}")
        mine = [stored for stored in self._logs.get((ctx.namespace, log_key), ()) if stored.run_id == run_id]
        if not can_resume(status_of(mine)):
            return None
        event = self._stamp(log_key, [resumed], ctx, origin)[0]
        await asyncio.sleep(0)
        return event

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        runs = [
            (log_key, event.run_id)
            for (namespace, log_key), log in self._logs.items()
            if namespace == ctx.namespace
            for event in log
            if event.kind in LIFECYCLE_KINDS
        ]
        # One fold per run through the port's own projection rather than a second inline
        # copy of it: re-walking a dev-sized dict is cheaper than two ways to derive status.
        summaries = []
        for log_key, run_id in dict.fromkeys(runs):
            found = await self.run_status(log_key, run_id, ctx)
            # Never None here: `runs` above only keeps pairs that already had a LIFECYCLE_KINDS
            # event, so `run_status` always has at least one event to fold — the `None` case
            # (no events at all) cannot occur for a run this loop found in the first place.
            assert found is not None
            summaries.append(RunSummary(log_key=log_key, run_id=run_id, status=found))
        return [summary for summary in summaries if status is None or summary.status is status]


# The unique-index equivalent this store used to need is gone: no caller supplies a ``seq``, and
# ``_stamp`` reads the run's last one and extends the log with no suspension in between, so two
# events at one ``seq`` is unconstructible rather than merely refused (ADR-D11 §6).


def _by_run(log: Sequence[Event]) -> dict[str, list[Event]]:
    """One log's events split per run, each list still in append order."""
    runs: dict[str, list[Event]] = {}
    for event in log:
        runs.setdefault(event.run_id, []).append(event)
    return runs


__all__ = ["MemoryEventStore"]
