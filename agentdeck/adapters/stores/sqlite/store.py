"""The event log in SQLite: the same contract as ``adapters.stores.memory``, durable.

New code, not a port of ``runtime/sessions.py`` — that module is engine-private execution
state (ADR-D5), a different store with a different owner. This one is the platform record:
append-only, one row per event, ``seq`` scoped to one run, unique across the whole namespace
rather than within one log — a run's id, not its log grouping, is what makes it distinct.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import suppress
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from agentdeck.core.events import Event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, STATES, can_resume, status_of
from agentdeck.errors import DuplicateKeyError, StoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import timedelta
    from pathlib import Path

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload, RunResumed, RunStarted
    from agentdeck.core.status import RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    log_key TEXT NOT NULL,
    run_id TEXT NOT NULL,
    key TEXT,
    seq INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_log ON events (namespace, log_key, id);
CREATE UNIQUE INDEX IF NOT EXISTS events_by_run ON events (namespace, run_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS events_by_key ON events (namespace, key) WHERE key IS NOT NULL;
"""
# events_by_run is the run-scoped identity guard: one seq per run is the promise consumers
# refetch a gap with, and a duplicate is the one corruption a gap check cannot see. Scoped to
# `(namespace, run_id, seq)` rather than `(namespace, log_key, run_id, seq)` — the shape this
# build inherited — because the old index let the same run_id+seq exist under two log keys,
# which is exactly "one logical run split across two logs", the defect #324 closes. Dropped and
# rebuilt by `_migrate_events_schema` below for a database an earlier build already created.
#
# events_by_key is `(namespace, key)`'s enforcement, partial so unkeyed rows (the overwhelming
# majority — every row but a run's own opening one) never compete for the constraint. A run
# whose key collides with another run's fails the INSERT itself; there is no read-then-write
# window for two racing claims to both pass through.

_INSERT = "INSERT INTO events (namespace, log_key, run_id, key, seq, data) VALUES (?, ?, ?, ?, ?, ?)"

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
        _migrate_events_schema(conn)
        conn.executescript(_SCHEMA)
        conn.commit()
    except sqlite3.Error as exc:
        raise StoreError(f"cannot open the event log at {db_path!r}: {exc}") from exc
    return conn


def _migrate_events_schema(conn: sqlite3.Connection) -> None:
    """Carry an ``events`` table an earlier build created forward to this build's shape.

    A brand-new file has no ``events`` table yet, so there is nothing to carry — ``_SCHEMA``
    below creates the current shape directly and this is a no-op. An existing one is missing
    the ``key`` column outright (``ALTER TABLE ... ADD COLUMN`` is additive, never destructive)
    and its ``events_by_run`` index is still scoped to ``(namespace, log_key, run_id, seq)``,
    the shape #324 tightens to ``(namespace, run_id, seq)``. Same name, different columns, so
    ``CREATE UNIQUE INDEX IF NOT EXISTS`` in ``_SCHEMA`` would find the name taken and leave the
    old, looser shape in place rather than tightening it — dropped here so the fresh ``CREATE``
    rebuilds it. If existing rows genuinely violate the tighter constraint (the same run_id and
    seq recorded under two different log keys), that ``CREATE UNIQUE INDEX`` fails and the
    ``StoreError`` it raises names the underlying conflict — the correct outcome: silently
    picking a survivor row would hide a real corruption rather than surface it.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if not columns:
        return
    if "key" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN key TEXT")
    # Only drop and rebuild when the index is genuinely the old shape: on a database already at
    # this build's schema (freshly created, or already migrated), `events_by_run` already reads
    # `(namespace, run_id, seq)`, and dropping it unconditionally on every open would force a
    # write — and the write lock that comes with it — where opening an up-to-date file used to
    # need none, which a peer mid-transaction would then see as a false contention.
    run_index_columns = [row[2] for row in conn.execute("PRAGMA index_info(events_by_run)")]
    if "log_key" in run_index_columns:
        conn.execute("DROP INDEX events_by_run")
    # `events_by_run_id` existed only for `locate`'s "which log holds this run_id" query, which
    # #322 removed with no caller left for it — a database built before this drops it too, rather
    # than carrying an index nothing queries through for good.
    conn.execute("DROP INDEX IF EXISTS events_by_run_id")


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

    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        return await self._run(partial(self._append, log_key, list(payloads), ctx, origin), "append")

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        rows = await self._run(partial(self._select_log, ctx.namespace_key, log_key, max(offset, 0), limit), "read")
        return [Event.model_validate(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        rows = await self._run(partial(self._select_run, ctx.namespace_key, log_key, run_id, from_seq), "read_run")
        return [Event.model_validate(json.loads(row)) for row in rows]

    async def claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        """The port's session claim as one ``BEGIN IMMEDIATE`` transaction, for the same reason
        ``claim_resume`` is one: the file's write lock, not this process, is what two servers
        agree through, so only one of them can open a run on an idle session.

        A refused claim is still a clean answer — the loser waited out the winner's transaction
        and then read the run the winner opened. Only a lock held past the busy timeout raises,
        because that is a store nobody can write to rather than a session somebody else took.
        """
        return await self._run(partial(self._claim_start, log_key, opening, ctx, origin, stale_after), "claim_start")

    async def claim_resume(
        self, log_key: str, run_id: str, resumed: RunResumed, ctx: RunContext, origin: str
    ) -> Event | None:
        """The port's conditional append as one ``BEGIN IMMEDIATE`` transaction, so the
        winner is decided by SQLite's own write lock — the file, not this process, is what
        two servers agree through.

        A loser still gets its clean ``None``: it waits for the winner's transaction to
        commit, then reads the ``RUNNING`` status the winner published. Only a lock held past
        the busy timeout raises, and that is a store nobody can write to rather than a claim
        somebody else won — ``StoreError``, never a fabricated ``None``.
        """
        if ctx.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot be made in the context of {ctx.run_id!r}")
        return await self._run(partial(self._claim, log_key, resumed, ctx, origin), "claim_resume")

    async def list_runs(
        self, ctx: RunContext, status: RunStatus | None = None, limit: int | None = None
    ) -> list[RunSummary]:
        """Overrides the port's per-run fold: one statement returns each run's *last*
        lifecycle row, so a listing deserializes one event per run instead of all of them."""
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        rows = await self._run(partial(self._select_last_lifecycle, ctx.namespace_key), "list_runs")
        # The filter never drops a row: the query selects lifecycle kinds only, so every row
        # folds to a status. It is what narrows ``status_of``'s ``None`` — the "no transition at
        # all" answer, which a run found by this query cannot give.
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=folded)
            for log_key, run_id, data in rows
            if (folded := status_of([Event.model_validate(json.loads(data))])) is not None
        ]
        filtered = [summary for summary in summaries if status is None or summary.status is status]
        return filtered if limit is None else filtered[:limit]

    async def find_by_key(self, ctx: RunContext, key: str) -> str | None:
        return await self._run(partial(self._select_run_by_key, ctx.namespace_key, key), "find_by_key")

    def _select_run_by_key(self, namespace: str, key: str) -> str | None:
        # `events_by_key` is a unique index over exactly these two columns, so this is the
        # index's own lookup rather than a scan — the same query the INSERT it guards runs
        # implicitly to decide whether to conflict.
        cursor = self._conn.execute(
            "SELECT run_id FROM events WHERE namespace = ? AND key = ? LIMIT 1", (namespace, key)
        )
        row = cursor.fetchone()
        return row[0] if row is not None else None

    def _append(self, log_key: str, payloads: list[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        if not payloads:  # as postgres and redis do — no reason to take the write lock for nothing
            return []
        # BEGIN IMMEDIATE, which a plain append never used to take. Reading MAX(seq) inside a
        # *deferred* transaction and then inserting upgrades read→write mid-transaction, which
        # SQLite answers with SQLITE_BUSY_SNAPSHOT — and it does not honour busy_timeout (#84),
        # so a peer committing in between is an error rather than a wait. Taking the write lock
        # first makes the read and the insert one step, which is the whole decision (ADR-D11).
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            return self._stamp_and_insert(log_key, payloads, ctx, origin)

    def _stamp_and_insert(
        self, log_key: str, payloads: list[KnownPayload], ctx: RunContext, origin: str, key: str | None = None
    ) -> list[Event]:
        """Assign, build, insert — callable only with the write lock already held.

        Every payload in one call shares one ``ts``: the batch is a single indivisible write, so
        it happened at one instant. Read from SQLite rather than this process, so N workers on one
        file compare one clock (ADR-D11 §4).

        ``key`` is written on the batch's first row only, never on the rest: a run's key is
        adopted once, by :meth:`claim_start`'s own call for the opening event, and every other
        write for this run — including every other call this method serves — passes ``None``.
        Repeating it on every row would collide with itself under ``events_by_key``.
        """
        now = self._backend_now()
        seq = self._select_last_seq(ctx.namespace_key, log_key, ctx.run_id)
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
                    ts=now,
                    payload=payload,
                )
            )
        self._conn.executemany(
            _INSERT,
            [
                (
                    ctx.namespace_key,
                    log_key,
                    event.run_id,
                    key if index == 0 else None,
                    event.seq,
                    event.model_dump_json(),
                )
                for index, event in enumerate(events)
            ],
        )
        return events

    def _backend_now(self) -> datetime:
        """SQLite's clock, to millisecond precision.

        ``CURRENT_TIMESTAMP`` is whole seconds, which would give every event in a busy second the
        same ``ts`` — visible coarsening on the wire for no reason. ``%f`` is seconds with three
        decimals, so the format below is the ISO string with an explicit UTC offset.
        """
        cursor = self._conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')")
        return datetime.fromisoformat(cursor.fetchone()[0])

    def _claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        # Same lock-first reasoning as _claim: BEGIN IMMEDIATE takes the write lock before the
        # reads, so a peer cannot open a run in the gap between finding this session idle and
        # saying so. A deferred transaction would only lock at the insert, which is too late.
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            stale_before = self._backend_now() - stale_after
            overridden: list[Event] = []
            for _run_id, status, last in self._select_open_runs(ctx.namespace_key, log_key):
                if STATES[status].suspended:
                    # No worker to be dead: PAUSED and WAITING_ANSWER have no engine polling a
                    # clock, so silence is not evidence of anything and the timer does not
                    # apply — the log deciding alone is what makes this hold permanent.
                    return SessionClaim(held_by=last.run_id), None
                if last.ts > stale_before:
                    return SessionClaim(held_by=last.run_id), None
                overridden.append(last)
            try:
                event = self._stamp_and_insert(log_key, [opening], ctx, origin, key=ctx.key)[0]
            except sqlite3.IntegrityError as exc:
                # `ctx.run_id` is freshly minted for every claim_start call, so the run-scoped
                # `events_by_run` index (namespace, run_id, seq) cannot fire here — a fresh id
                # and seq 0 have never been seen before. The one constraint left that can is
                # `events_by_key`, and only when this run actually carries a key.
                if ctx.key is not None:
                    # The insert already failed, so the row that holds the key is a plain read
                    # away — naming it here is what a caller refused a duplicate start acts on.
                    holder = self._select_run_by_key(ctx.namespace_key, ctx.key)
                    raise DuplicateKeyError(
                        f"key {ctx.key!r} is already used by run {holder!r} in namespace {ctx.namespace!r}"
                    ) from exc
                raise
        return SessionClaim(overridden=tuple(overridden)), event

    def _select_open_runs(self, namespace: str, log_key: str) -> list[tuple[str, RunStatus, Event]]:
        """Every run in this log that has recorded a transition but not a terminal one, paired
        with its own status and its own last event — whatever kind — because that event is the
        run's last sign of life, and silence is all that separates an abandoned run from a
        working one.
        """
        cursor = self._conn.execute(
            "SELECT run_id, data, MAX(id) FROM events "
            f"WHERE namespace = ? AND log_key = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "GROUP BY run_id",
            (namespace, log_key, *_SORTED_LIFECYCLE_KINDS),
        )
        open_runs = [
            (row[0], status)
            for row in cursor.fetchall()
            if (status := status_of([Event.model_validate(json.loads(row[1]))])) is not None
            and not STATES[status].terminal
        ]
        return [(run_id, status, self._select_last_event(namespace, log_key, run_id)) for run_id, status in open_runs]

    def _select_last_event(self, namespace: str, log_key: str, run_id: str) -> Event:
        cursor = self._conn.execute(
            "SELECT data, MAX(id) FROM events WHERE namespace = ? AND log_key = ? AND run_id = ?",
            (namespace, log_key, run_id),
        )
        return Event.model_validate(json.loads(cursor.fetchone()[0]))

    def _claim(self, log_key: str, resumed: RunResumed, ctx: RunContext, origin: str) -> Event | None:
        # BEGIN IMMEDIATE takes the file's write lock before the reads, so a second process
        # cannot see this run waiting in the gap between our check and our insert — a
        # deferred transaction would only lock at the insert, which is exactly too late.
        self._conn.execute("BEGIN IMMEDIATE")
        # Commits the insert on the way out, rolls back if anything raised; the losing path
        # wrote nothing, so its commit is only the write lock being handed back.
        with self._conn:
            last = self._select_last_lifecycle_of_run(ctx.namespace_key, log_key, ctx.run_id)
            if not can_resume(status_of([Event.model_validate(json.loads(last))] if last is not None else [])):
                return None
            return self._stamp_and_insert(log_key, [resumed], ctx, origin)[0]

    def _select_log(self, namespace: str, log_key: str, after: int, limit: int | None) -> list[str]:
        # SQLite treats a negative LIMIT as "no limit" — the one case a plain int can't say.
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE namespace = ? AND log_key = ? ORDER BY id ASC LIMIT ? OFFSET ?",
            (namespace, log_key, -1 if limit is None else limit, after),
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_run(self, namespace: str, log_key: str, run_id: str, from_seq: int) -> list[str]:
        cursor = self._conn.execute(
            "SELECT data FROM events WHERE namespace = ? AND log_key = ? AND run_id = ? AND seq >= ? ORDER BY id ASC",
            (namespace, log_key, run_id, from_seq),
        )
        return [row[0] for row in cursor.fetchall()]

    def _select_last_seq(self, namespace: str, log_key: str, run_id: str) -> int:
        cursor = self._conn.execute(
            "SELECT MAX(seq) FROM events WHERE namespace = ? AND log_key = ? AND run_id = ?",
            (namespace, log_key, run_id),
        )
        row = cursor.fetchone()
        return row[0] if row is not None and row[0] is not None else -1

    def _select_last_lifecycle_of_run(self, namespace: str, log_key: str, run_id: str) -> str | None:
        cursor = self._conn.execute(
            "SELECT data FROM events "
            f"WHERE namespace = ? AND log_key = ? AND run_id = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "ORDER BY id DESC LIMIT 1",
            (namespace, log_key, run_id, *_SORTED_LIFECYCLE_KINDS),
        )
        row = cursor.fetchone()
        return row[0] if row is not None else None

    def _select_last_lifecycle(self, namespace: str) -> list[tuple[str, str, str]]:
        # SQLite guarantees the bare columns of a MAX() group come from the row that held the
        # maximum, so this is the newest lifecycle event of each run in a single group-by.
        cursor = self._conn.execute(
            "SELECT log_key, run_id, data, MAX(id) FROM events "
            f"WHERE namespace = ? AND json_extract(data, '$.kind') IN ({_KIND_SLOTS}) "
            "GROUP BY log_key, run_id",
            (namespace, *_SORTED_LIFECYCLE_KINDS),
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StoreError(f"closing the event log failed: {exc}") from exc


__all__ = ["SqliteEventStore"]
