"""The event log in SQLite: the same contract as ``adapters.stores.memory``, durable.

New code, not a port of ``runtime/sessions.py`` — that module is engine-private execution
state (ADR-D5), a different store with a different owner. This one is the platform record:
append-only, one row per event, ``seq`` scoped to one run within one log.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING

from agentdeck.core.events import parse_event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, can_resume, status_of
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
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

# Pinned here rather than inherited from ``sqlite3.connect``'s own default: long enough that a
# peer's write transaction — milliseconds of one append — is waited out rather than raised
# over, short enough that a wedged holder surfaces as an error instead of hanging a request.
_BUSY_TIMEOUT_MS = 5_000


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the log with the concurrency posture two processes need, and its table."""
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        # Before the journal mode, so the switch itself can wait a peer's transaction out.
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        _enable_wal(conn)
        conn.executescript(_SCHEMA)
        conn.commit()
    except sqlite3.Error as exc:
        raise StoreError(f"cannot open the event log at {db_path!r}: {exc}") from exc
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Put the file in WAL, settling for the mode it already has if it cannot be switched now.

    Converting *into* WAL needs an exclusive lock, and a peer holding the write lock denies
    that outright — SQLite refuses immediately there, whatever the busy timeout says. Asking
    again on a file that is already WAL is free even mid-write, so the only connection that
    can lose this is the first one to a brand-new file racing another; it then runs in the
    rollback-journal mode every connection used before WAL was asked for at all, which is
    slower under contention and never wrong. An in-memory database reports ``memory`` and
    stays there — there is no WAL for it to switch to, and nothing to work around.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        return
    # ponytail: the degraded mode is invisible — log it if an operator ever has to find out
    # why one process's store came up slower than its peers'.
    with suppress(sqlite3.OperationalError):
        conn.execute("PRAGMA journal_mode = WAL")


class SqliteEventStore(EventStorePort):
    """Append-only rows in one SQLite file (or ``:memory:`` for tests).

    One connection, serialized by a lock: ``sqlite3`` is stdlib but not coroutine-safe, and
    a single writer per log is exactly the contract the Runtime already assumes — a lock is
    simpler than a pool for that shape. Blocking calls run in a thread so the event loop is
    never stalled by disk I/O, and a failed statement reaches the caller as ``StoreError``:
    a ``sqlite3`` type never crosses the port.

    The file is put in **WAL** mode and every connection sets an explicit busy timeout,
    because the point of this store is that a second OS process reads and writes the same
    file: WAL lets those readers run while a writer appends, and the timeout makes a peer's
    in-flight write something to wait out rather than raise over. Two consequences for whoever
    operates it: SQLite keeps ``<db>-wal`` and ``<db>-shm`` files beside the database — copy
    or delete them with it, never just the one file — and WAL needs working shared memory
    across processes, so it is unreliable on network filesystems like NFS or SMB. Keep the
    events file on local disk; a networked deployment wants the Redis or Postgres store.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = _connect(str(db_path))
        self._lock = asyncio.Lock()

    async def _run[T](self, work: Callable[[], T], op: str) -> T:
        """Every statement this store runs goes through here — one caller at a time, off the
        event loop, and no library exception escaping the port."""
        async with self._lock:
            try:
                return await asyncio.to_thread(work)
            except sqlite3.Error as exc:
                raise StoreError(f"event log {op} failed: {exc}") from exc

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        foreign = {event.tenant for event in events} - {ctx.tenant}
        if foreign:
            raise ValueError(f"events for tenant(s) {sorted(foreign)} cannot be written to {ctx.tenant!r}'s log")
        rows = [(ctx.tenant, log_key, event.run_id, event.seq, event.model_dump_json()) for event in events]
        await self._run(partial(self._insert, rows), "append")

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        rows = await self._run(partial(self._select_log, ctx.tenant, log_key, max(offset, 0), limit), "read")
        return [parse_event(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        rows = await self._run(partial(self._select_run, ctx.tenant, log_key, run_id, from_seq), "read_run")
        return [parse_event(json.loads(row)) for row in rows]

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        return await self._run(partial(self._select_last_seq, ctx.tenant, log_key, run_id), "last_seq")

    async def claim_start(self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime) -> SessionClaim:
        """The port's session claim as one ``BEGIN IMMEDIATE`` transaction, for the same reason
        ``claim_resume`` is one: the file's write lock, not this process, is what two servers
        agree through, so only one of them can open a run on an idle session.

        A refused claim is still a clean answer — the loser waited out the winner's transaction
        and then read the run the winner opened. Only a lock held past the busy timeout raises,
        because that is a store nobody can write to rather than a session somebody else took.
        """
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")
        row = (ctx.tenant, log_key, event.run_id, event.seq, event.model_dump_json())
        return await self._run(partial(self._claim_start, row, stale_before), "claim_start")

    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """The port's conditional append as one ``BEGIN IMMEDIATE`` transaction, so the
        winner is decided by SQLite's own write lock — the file, not this process, is what
        two servers agree through.

        A loser still gets its clean ``False``: it waits for the winner's transaction to
        commit, then reads the ``RUNNING`` status the winner published. Only a lock held past
        the busy timeout raises, and that is a store nobody can write to rather than a claim
        somebody else won — ``StoreError``, never a fabricated ``False``.
        """
        if event.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot carry an event for {event.run_id!r}")
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")
        row = (ctx.tenant, log_key, event.run_id, event.seq, event.model_dump_json())
        return await self._run(partial(self._claim, row, run_id), "claim_resume")

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Overrides the port's per-run fold: one statement returns each run's *last*
        lifecycle row, so a listing deserializes one event per run instead of all of them."""
        rows = await self._run(partial(self._select_last_lifecycle, ctx.tenant), "list_runs")
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=status_of([parse_event(json.loads(data))]))
            for log_key, run_id, data in rows
        ]
        return [summary for summary in summaries if status is None or summary.status is status]

    def _insert(self, rows: list[tuple[str, str, str, int, str]]) -> None:
        self._conn.executemany(_INSERT, rows)
        self._conn.commit()

    def _claim_start(self, row: tuple[str, str, str, int, str], stale_before: datetime) -> SessionClaim:
        tenant, log_key, _run_id, _seq, _data = row
        # Same lock-first reasoning as _claim: BEGIN IMMEDIATE takes the write lock before the
        # reads, so a peer cannot open a run in the gap between finding this session idle and
        # saying so. A deferred transaction would only lock at the insert, which is too late.
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            overridden: list[str] = []
            for run_id, last in self._select_open_runs(tenant, log_key):
                if last.ts > stale_before:
                    return SessionClaim(held_by=run_id)
                overridden.append(run_id)
            self._conn.execute(_INSERT, row)
        return SessionClaim(overridden=tuple(overridden))

    def _select_open_runs(self, tenant: str, log_key: str) -> list[tuple[str, Event]]:
        """Every run in this log that has recorded a transition but not a terminal one, paired
        with its own last event — whatever kind — because that event is the run's last sign of
        life, and silence is all that separates an abandoned run from a working one.
        """
        cursor = self._conn.execute(
            "SELECT run_id, data, MAX(id) FROM events "
            f"WHERE tenant = ? AND log_key = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "GROUP BY run_id",
            (tenant, log_key, *_SORTED_LIFECYCLE_KINDS),
        )
        open_runs = [
            row[0] for row in cursor.fetchall() if status_of([parse_event(json.loads(row[1]))]) not in TERMINAL_STATUSES
        ]
        return [(run_id, self._select_last_event(tenant, log_key, run_id)) for run_id in open_runs]

    def _select_last_event(self, tenant: str, log_key: str, run_id: str) -> Event:
        cursor = self._conn.execute(
            "SELECT data, MAX(id) FROM events WHERE tenant = ? AND log_key = ? AND run_id = ?",
            (tenant, log_key, run_id),
        )
        return parse_event(json.loads(cursor.fetchone()[0]))

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
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StoreError(f"closing the event log failed: {exc}") from exc


__all__ = ["SqliteEventStore"]
