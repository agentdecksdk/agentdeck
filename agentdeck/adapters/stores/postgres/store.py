"""The event log in Postgres: the same contract as ``adapters.stores.sqlite``, for workers
that share a database rather than a filesystem.

The SQLite store's own docstring points here for a networked deployment — WAL needs shared
memory across processes, so a log on NFS is unreliable, and that is exactly the shape a
multi-worker server has. Nothing else changes: append-only, one row per event, ``seq``
scoped to one run within one log, and status still derived by folding events rather than
stored (ADR-D5: the log is the sole source of truth).

Everything this store owns lives in its **own schema** (``agentdeck_events`` by default),
so a database that also holds the langgraph checkpointer's tables keeps the platform record
and the engine's private execution state apart — the operational separation ADR-D5 asks
for, expressed as the one thing Postgres can enforce.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql

from agentdeck.core.events import Event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, can_resume, status_of
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import timedelta

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload, RunResumed, RunStarted
    from agentdeck.core.status import RunStatus

    type Connection = psycopg.AsyncConnection[tuple[Any, ...]]

# Postgres's own wall clock, so N workers sharing one database compare one clock rather than N
# (ADR-D11 §4). ``clock_timestamp()`` and not ``now()``: the latter is the transaction's start
# time, so every event in a batch would carry the timestamp of the statement that opened it.
_SELECT_NOW = "SELECT clock_timestamp()"

_DEFAULT_SCHEMA = "agentdeck_events"

_SORTED_LIFECYCLE_KINDS = sorted(LIFECYCLE_KINDS)

# The SQLite store's busy timeout, in the one place Postgres spells the same idea: long
# enough to wait out a peer's claim (milliseconds of one transaction), short enough that a
# wedged holder surfaces as an error instead of hanging a request forever.
_LOCK_TIMEOUT_MS = 5_000


def _advisory_key(name: str) -> int:
    """A stable signed 64-bit lock number for ``name``.

    Hashed here rather than by Postgres's own ``hashtext`` because that function is an
    undocumented internal whose value is not promised across major versions; this one is
    the same number in every process and every server. Two different names colliding costs
    nothing but serializing two unrelated claims, which is why a 64-bit digest is plenty.
    """
    return int.from_bytes(hashlib.blake2b(name.encode(), digest_size=8).digest(), "big", signed=True)


class PostgresEventStore(EventStorePort):
    """Append-only rows in one Postgres schema, reachable by every worker at once.

    One connection per store instance, serialized by a lock — ``psycopg``'s async
    connection is not safe to drive from two coroutines at once, and a single writer per
    log is the shape the Runtime already assumes. Consequence to know when operating it:
    anything waiting on a peer's transaction holds this process's only connection, so every
    other call queues behind it. On the write and claim paths that wait is bounded by
    ``lock_timeout``; the one place it is not is first-use schema setup, whose lock is
    session-scoped and taken before any transaction exists, so a peer wedged midway through
    creating the schema blocks this instance until it finishes or its connection drops.

    Every write — both conditional appends and the plain one — takes a per-log advisory lock
    before reading or inserting anything, so two servers agree through Postgres and not
    through Python. ``READ COMMITTED`` is load-bearing for the claims, not incidental: the
    loser has to see the winner's committed rows after the lock is handed over, and a
    snapshot taken at the transaction's first statement — what ``REPEATABLE READ`` would
    give it — was taken before the winner committed. Since that first statement is the
    ``lock_timeout`` setting rather than the lock itself, the pin is the whole defence and
    not a second layer of one; the connection pins it so a server configured otherwise
    cannot quietly break the claim.

    A ``psycopg`` exception never crosses the port: everything funnels through ``_run``
    and reaches the caller as ``StoreError``.
    """

    def __init__(self, dsn: str, *, schema: str = _DEFAULT_SCHEMA) -> None:
        self._dsn = dsn
        self._schema = schema
        self._conn: Connection | None = None
        self._lock = asyncio.Lock()
        self._setup_key = _advisory_key(f"agentdeck:events:setup:{schema}")
        table = sql.Identifier(schema, "events")
        # Composed once: the schema name is an identifier, so it is quoted by psycopg
        # rather than interpolated — a schema is caller-supplied configuration.
        self._ddl = (
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(schema=sql.Identifier(schema)),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {table} ("
                " id BIGSERIAL PRIMARY KEY,"
                " namespace TEXT NOT NULL,"
                " log_key TEXT NOT NULL,"
                " run_id TEXT NOT NULL,"
                " seq INTEGER NOT NULL,"
                " data JSONB NOT NULL)"
            ).format(table=table),
            sql.SQL("CREATE INDEX IF NOT EXISTS events_by_log ON {table} (namespace, log_key, id)").format(table=table),
            # UNIQUE is the guard, not just the index: one seq per run is the promise consumers
            # refetch a gap with, and a duplicate is the one corruption a gap check cannot see.
            # ponytail: only schemas this build creates get the constraint — one from an earlier
            # beta keeps its non-unique index, and v2 has no migration story yet.
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS events_by_run ON {table} (namespace, log_key, run_id, seq)"
            ).format(table=table),
        )
        self._insert = sql.SQL(
            "INSERT INTO {table} (namespace, log_key, run_id, seq, data) VALUES (%s, %s, %s, %s, %s::jsonb)"
        ).format(table=table)
        self._select_log = sql.SQL(
            "SELECT data FROM {table} WHERE namespace = %s AND log_key = %s ORDER BY id ASC LIMIT %s OFFSET %s"
        ).format(table=table)
        self._select_run = sql.SQL(
            "SELECT data FROM {table} WHERE namespace = %s AND log_key = %s AND run_id = %s AND seq >= %s ORDER BY id ASC"
        ).format(table=table)
        self._select_last_seq = sql.SQL(
            "SELECT MAX(seq) FROM {table} WHERE namespace = %s AND log_key = %s AND run_id = %s"
        ).format(table=table)
        # DISTINCT ON is Postgres's version of the SQLite store's MAX(id) group-by: the
        # newest lifecycle row of each run, one row per run, one statement.
        self._select_last_lifecycle = sql.SQL(
            "SELECT DISTINCT ON (log_key, run_id) log_key, run_id, data FROM {table} "
            "WHERE namespace = %s AND data->>'kind' = ANY(%s) ORDER BY log_key, run_id, id DESC"
        ).format(table=table)
        self._select_run_lifecycle = sql.SQL(
            "SELECT data FROM {table} WHERE namespace = %s AND log_key = %s AND run_id = %s "
            "AND data->>'kind' = ANY(%s) ORDER BY id DESC LIMIT 1"
        ).format(table=table)
        self._select_log_lifecycle = sql.SQL(
            "SELECT DISTINCT ON (run_id) run_id, data FROM {table} "
            "WHERE namespace = %s AND log_key = %s AND data->>'kind' = ANY(%s) ORDER BY run_id, id DESC"
        ).format(table=table)
        self._select_last_events = sql.SQL(
            "SELECT DISTINCT ON (run_id) run_id, data FROM {table} "
            "WHERE namespace = %s AND log_key = %s AND run_id = ANY(%s) ORDER BY run_id, id DESC"
        ).format(table=table)

    async def _run[T](self, work: Callable[[Connection], Awaitable[T]], op: str) -> T:
        """Every statement this store runs goes through here — one caller at a time, and no
        ``psycopg`` exception escaping the port."""
        async with self._lock:
            try:
                return await work(await self._ready())
            except psycopg.Error as exc:
                # A connection that died stays dead otherwise: drop it so the next call
                # dials again instead of failing forever on a socket nobody is holding.
                if self._conn is not None and self._conn.closed:
                    self._conn = None
                raise StoreError(f"event log {op} failed: {exc}") from exc

    async def _ready(self) -> Connection:
        """Connect and create the schema on first use — never at construction, so building
        this store is not I/O and a composition root can wire one without a live server."""
        if self._conn is None:
            conn: Connection = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
            # Closed rather than abandoned if setup fails: this is the retried path, and a
            # connection nothing holds a reference to holds a server backend regardless.
            try:
                await conn.set_isolation_level(psycopg.IsolationLevel.READ_COMMITTED)
                # Two workers starting together would otherwise race their own CREATEs: the
                # IF NOT EXISTS checks are not atomic against each other, and the loser gets
                # a duplicate-object error rather than the table it asked for.
                await conn.execute("SELECT pg_advisory_lock(%s)", (self._setup_key,))
                try:
                    for statement in self._ddl:
                        await conn.execute(statement)
                finally:
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (self._setup_key,))
            except BaseException:
                await conn.close()
                raise
            self._conn = conn
        return self._conn

    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        """Writes take the log's lock, for the port's paging guarantee and now for ``seq``.

        Row order is `BIGSERIAL`, assigned at insert and published at commit, so an
        unlocked append can be given a *later* number than a claim's in-flight insert and
        still commit *first* — the claim's event then appears at an offset a reader has
        already gone past, and one of its neighbours is delivered twice. Serializing writes
        per log is what keeps the log growing only at its end. The same lock is what makes
        reading this run's last ``seq`` and inserting the next one indivisible.
        """
        if not payloads:
            # No lock and no transaction for a batch with nothing in it, as in the Redis store.
            return []

        async def _work(conn: Connection) -> list[Event]:
            async with conn.transaction():
                await self._lock_log(conn, ctx.namespace_key, log_key)
                return await self._stamp_and_insert(conn, log_key, list(payloads), ctx, origin)

        return await self._run(_work, "append")

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")

        async def _work(conn: Connection) -> list[dict[str, Any]]:
            # LIMIT NULL is Postgres for "no limit", which is what the port's None means.
            cursor = await conn.execute(self._select_log, (ctx.namespace_key, log_key, limit, max(offset, 0)))
            return [row[0] for row in await cursor.fetchall()]

        return [Event.model_validate(data) for data in await self._run(_work, "read")]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        async def _work(conn: Connection) -> list[dict[str, Any]]:
            cursor = await conn.execute(self._select_run, (ctx.namespace_key, log_key, run_id, from_seq))
            return [row[0] for row in await cursor.fetchall()]

        return [Event.model_validate(data) for data in await self._run(_work, "read_run")]

    async def _stamp_and_insert(
        self, conn: Connection, log_key: str, payloads: list[KnownPayload], ctx: RunContext, origin: str
    ) -> list[Event]:
        """Assign, build, insert — callable only with this log's advisory lock already held.

        ``ts`` is ``clock_timestamp()``, Postgres's own wall clock, so N workers sharing one
        database compare one clock rather than N (ADR-D11 §4). ``clock_timestamp`` rather than
        ``now()``, which is the transaction's start time and would hand every event in a batch
        the timestamp of the statement that opened it.
        """
        cursor = await conn.execute(_SELECT_NOW)
        now = (await cursor.fetchone())[0]  # ty: ignore[not-subscriptable] — one-row scalar select
        seq = await self._last_seq(conn, ctx.namespace_key, log_key, ctx.run_id)
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
        await conn.cursor().executemany(self._insert, [_row(ctx.namespace_key, log_key, event) for event in events])
        return events

    async def claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        """The port's session claim as one transaction holding this log's advisory lock, so
        only one of two servers can open a run on an idle session.

        A refused claim is still a clean answer: the loser waits the winner's transaction
        out and then reads the run it opened. Only a lock held past ``lock_timeout`` raises,
        because that is a store nobody can write to rather than a session somebody took.
        """

        async def _work(conn: Connection) -> tuple[SessionClaim, Event | None]:
            async with conn.transaction():
                await self._lock_log(conn, ctx.namespace_key, log_key)
                cursor = await conn.execute(_SELECT_NOW)
                stale_before = (await cursor.fetchone())[0] - stale_after  # ty: ignore[not-subscriptable]
                overridden: list[Event] = []
                for _run_id, last in await self._open_runs(conn, ctx.namespace_key, log_key):
                    if last.ts > stale_before:
                        return SessionClaim(held_by=last.run_id), None
                    overridden.append(last)
                event = (await self._stamp_and_insert(conn, log_key, [opening], ctx, origin))[0]
            return SessionClaim(overridden=tuple(overridden)), event

        return await self._run(_work, "claim_start")

    async def claim_resume(
        self, log_key: str, run_id: str, resumed: RunResumed, ctx: RunContext, origin: str
    ) -> Event | None:
        """The port's conditional append as one transaction holding this log's advisory
        lock: the lock is taken before the read, so a peer cannot resume the same run in
        the gap between this caller's check and its insert.

        A loser gets its clean ``None`` — it reads the ``RUNNING`` status the winner
        published. Only an unreachable store or a lock held past ``lock_timeout`` raises,
        never a fabricated ``None``.
        """
        if ctx.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot be made in the context of {ctx.run_id!r}")

        async def _work(conn: Connection) -> Event | None:
            async with conn.transaction():
                await self._lock_log(conn, ctx.namespace_key, log_key)
                last = await self._last_lifecycle_of_run(conn, ctx.namespace_key, log_key, run_id)
                if not can_resume(status_of([last] if last is not None else [])):
                    return None
                return (await self._stamp_and_insert(conn, log_key, [resumed], ctx, origin))[0]

        return await self._run(_work, "claim_resume")

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Overrides the port's per-run fold: one statement returns each run's *last*
        lifecycle row, so a listing deserializes one event per run instead of all of them."""

        async def _work(conn: Connection) -> list[tuple[str, str, dict[str, Any]]]:
            cursor = await conn.execute(self._select_last_lifecycle, (ctx.namespace_key, _SORTED_LIFECYCLE_KINDS))
            return [(row[0], row[1], row[2]) for row in await cursor.fetchall()]

        # The filter never drops a row: the query selects lifecycle kinds only, so every row
        # folds to a status. It is what narrows ``status_of``'s ``None`` — the "no transition at
        # all" answer, which a run found by this query cannot give.
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=folded)
            for log_key, run_id, data in await self._run(_work, "list_runs")
            if (folded := status_of([Event.model_validate(data)])) is not None
        ]
        return [summary for summary in summaries if status is None or summary.status is status]

    async def _lock_log(self, conn: Connection, namespace: str, log_key: str) -> None:
        """Serialize this log's writes, and bound the wait.

        The lock is per (namespace, log key) and transaction-scoped, so it is released by the
        commit that publishes the write and never outlives a crashed worker. It is taken
        before any read the decision depends on, which is the whole point: a lock acquired
        afterwards would leave the same check-then-write window a plain read has.
        """
        await conn.execute("SELECT set_config('lock_timeout', %s, true)", (f"{_LOCK_TIMEOUT_MS}ms",))
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_advisory_key(f"{namespace}\x00{log_key}"),))

    async def _last_seq(self, conn: Connection, namespace: str, log_key: str, run_id: str) -> int:
        cursor = await conn.execute(self._select_last_seq, (namespace, log_key, run_id))
        row = await cursor.fetchone()
        return row[0] if row is not None and row[0] is not None else -1

    async def _last_lifecycle_of_run(self, conn: Connection, namespace: str, log_key: str, run_id: str) -> Event | None:
        cursor = await conn.execute(self._select_run_lifecycle, (namespace, log_key, run_id, _SORTED_LIFECYCLE_KINDS))
        row = await cursor.fetchone()
        return Event.model_validate(row[0]) if row is not None else None

    async def _open_runs(self, conn: Connection, namespace: str, log_key: str) -> list[tuple[str, Event]]:
        """Every run in this log that has recorded a transition but not a terminal one,
        paired with its own last event — whatever kind — because that event is the run's
        last sign of life, and silence is all that separates an abandoned run from a
        working one.
        """
        cursor = await conn.execute(self._select_log_lifecycle, (namespace, log_key, _SORTED_LIFECYCLE_KINDS))
        open_runs = [
            row[0]
            for row in await cursor.fetchall()
            if status_of([Event.model_validate(row[1])]) not in TERMINAL_STATUSES
        ]
        if not open_runs:
            return []
        cursor = await conn.execute(self._select_last_events, (namespace, log_key, open_runs))
        last_events = {row[0]: Event.model_validate(row[1]) for row in await cursor.fetchall()}
        return [(run_id, last_events[run_id]) for run_id in open_runs]

    async def aclose(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
        except psycopg.Error as exc:
            raise StoreError(f"closing the event log failed: {exc}") from exc


def _row(namespace: str, log_key: str, event: Event) -> tuple[str, str, str, int, str]:
    return (namespace, log_key, event.run_id, event.seq, event.model_dump_json())


__all__ = ["PostgresEventStore"]
