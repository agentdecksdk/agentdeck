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
from agentdeck.core.ports import SessionStorePort

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event

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


class SqliteEventStore(SessionStorePort):
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

    async def read(self, log_key: str, ctx: RunContext) -> list[Event]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select_log, ctx.tenant, log_key)
        return [parse_event(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select_run, ctx.tenant, log_key, run_id, from_seq)
        return [parse_event(json.loads(row)) for row in rows]

    async def list_log_keys(self, ctx: RunContext) -> list[str]:
        async with self._lock:
            return await asyncio.to_thread(self._select_log_keys, ctx.tenant)

    def _insert(self, rows: list[tuple[str, str, str, int, str]]) -> None:
        self._conn.executemany("INSERT INTO events (tenant, log_key, run_id, seq, data) VALUES (?, ?, ?, ?, ?)", rows)
        self._conn.commit()

    def _select_log(self, tenant: str, log_key: str) -> list[str]:
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE tenant = ? AND log_key = ? ORDER BY id ASC", (tenant, log_key)
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_run(self, tenant: str, log_key: str, run_id: str, from_seq: int) -> list[str]:
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE tenant = ? AND log_key = ? AND run_id = ? AND seq >= ? ORDER BY id ASC",
            (tenant, log_key, run_id, from_seq),
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_log_keys(self, tenant: str) -> list[str]:
        cursor = self._conn.execute("SELECT DISTINCT log_key FROM events WHERE tenant = ?", (tenant,))
        return [row[0] for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()


__all__ = ["SqliteEventStore"]
