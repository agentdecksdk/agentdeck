"""The event log in SQLite: the same contract as ``adapters.stores.memory``, durable.

New code, not a port of ``runtime/sessions.py`` — that module is engine-private execution
state (ADR-D5), a different store with a different owner. This one is the platform record:
append-only, one row per event, ``seq`` scoped to one run within one log.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING

from agentdeck.core.events import parse_event
from agentdeck.core.ports import EventStorePort, RunSummary
from agentdeck.core.status import LIFECYCLE_KINDS, can_resume, status_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.core.status import RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    log_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_log ON events (tenant, log_key, id);
CREATE INDEX IF NOT EXISTS events_by_run ON events (tenant, log_key, run_id, seq);
"""

_INSERT = "INSERT INTO events (tenant, log_key, run_id, seq, data) VALUES (?, ?, ?, ?, ?)"

_SORTED_LIFECYCLE_KINDS = tuple(sorted(LIFECYCLE_KINDS))
_KIND_SLOTS = ", ".join("?" * len(_SORTED_LIFECYCLE_KINDS))


class SqliteEventStore(EventStorePort):
    """Append-only rows in one SQLite file (or ``:memory:`` for tests).

    One connection, serialized by a lock: ``sqlite3`` is stdlib but not coroutine-safe, and
    a single writer per log is exactly the WAL-style contract the Runtime already assumes —
    a lock is simpler than a pool for that shape. Blocking calls run in a thread so the
    event loop is never stalled by disk I/O.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        foreign = {event.tenant for event in events} - {ctx.tenant}
        if foreign:
            raise ValueError(f"events for tenant(s) {sorted(foreign)} cannot be written to {ctx.tenant!r}'s log")
        rows = [(ctx.tenant, log_key, event.run_id, event.seq, event.model_dump_json()) for event in events]
        async with self._lock:
            await asyncio.to_thread(self._insert, rows)

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        async with self._lock:
            rows = await asyncio.to_thread(self._select_log, ctx.tenant, log_key, max(offset, 0), limit)
        return [parse_event(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select_run, ctx.tenant, log_key, run_id, from_seq)
        return [parse_event(json.loads(row)) for row in rows]

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._select_last_seq, ctx.tenant, log_key, run_id)

    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """The port's conditional append as one ``BEGIN IMMEDIATE`` transaction, so the
        winner is decided by SQLite's own write lock — the file, not this process, is what
        two servers agree through."""
        if event.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot carry an event for {event.run_id!r}")
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")
        row = (ctx.tenant, log_key, event.run_id, event.seq, event.model_dump_json())
        async with self._lock:
            return await asyncio.to_thread(self._claim, row, run_id)

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Overrides the port's per-run fold: one statement returns each run's *last*
        lifecycle row, so a listing deserializes one event per run instead of all of them."""
        async with self._lock:
            rows = await asyncio.to_thread(self._select_last_lifecycle, ctx.tenant)
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=status_of([parse_event(json.loads(data))]))
            for log_key, run_id, data in rows
        ]
        return [summary for summary in summaries if status is None or summary.status is status]

    def _insert(self, rows: list[tuple[str, str, str, int, str]]) -> None:
        self._conn.executemany(_INSERT, rows)
        self._conn.commit()

    def _claim(self, row: tuple[str, str, str, int, str], run_id: str) -> bool:
        tenant, log_key, _run_id, seq, _data = row
        # BEGIN IMMEDIATE takes the file's write lock before the reads, so a second process
        # cannot see this run waiting in the gap between our checks and our insert — a
        # deferred transaction would only lock at the insert, which is exactly too late.
        self._conn.execute("BEGIN IMMEDIATE")
        # Commits the insert on the way out, rolls back if anything raised; the losing paths
        # wrote nothing, so their commit is only the write lock being handed back.
        with self._conn:
            last = self._select_last_lifecycle_of_run(tenant, log_key, run_id)
            if not can_resume(status_of([parse_event(json.loads(last))] if last is not None else [])):
                return False
            if seq != self._select_last_seq(tenant, log_key, run_id) + 1:
                # The run went round the loop while this claim was in flight: it waits again,
                # but on a longer log, and this seq now belongs to an event already written.
                return False
            self._conn.execute(_INSERT, row)
        return True

    def _select_log(self, tenant: str, log_key: str, after: int, limit: int | None) -> list[str]:
        # SQLite treats a negative LIMIT as "no limit" — the one case a plain int can't say.
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE tenant = ? AND log_key = ? ORDER BY id ASC LIMIT ? OFFSET ?",
            (tenant, log_key, -1 if limit is None else limit, after),
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_run(self, tenant: str, log_key: str, run_id: str, from_seq: int) -> list[str]:
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE tenant = ? AND log_key = ? AND run_id = ? AND seq >= ? ORDER BY id ASC",
            (tenant, log_key, run_id, from_seq),
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_last_seq(self, tenant: str, log_key: str, run_id: str) -> int:
        cursor = self._conn.execute(
            "SELECT MAX(seq) FROM events WHERE tenant = ? AND log_key = ? AND run_id = ?", (tenant, log_key, run_id)
        )
        row = cursor.fetchone()
        return row[0] if row is not None and row[0] is not None else -1

    def _select_last_lifecycle_of_run(self, tenant: str, log_key: str, run_id: str) -> str | None:
        cursor = self._conn.execute(
            "SELECT data FROM events "
            f"WHERE tenant = ? AND log_key = ? AND run_id = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "ORDER BY id DESC LIMIT 1",
            (tenant, log_key, run_id, *_SORTED_LIFECYCLE_KINDS),
        )
        row = cursor.fetchone()
        return row[0] if row is not None else None

    def _select_last_lifecycle(self, tenant: str) -> list[tuple[str, str, str]]:
        # SQLite guarantees the bare columns of a MAX() group come from the row that held the
        # maximum, so this is the newest lifecycle event of each run in a single group-by.
        cursor = self._conn.execute(
            "SELECT log_key, run_id, data, MAX(id) FROM events "
            f"WHERE tenant = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "GROUP BY log_key, run_id",
            (tenant, *_SORTED_LIFECYCLE_KINDS),
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()


__all__ = ["SqliteEventStore"]
