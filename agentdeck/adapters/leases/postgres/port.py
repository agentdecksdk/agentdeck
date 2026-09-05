"""Minimal Postgres ``LeasePort`` paired with the Postgres control backend.

This keeps ``AGENTDECK_CONTROL=postgresql://...`` internally consistent: control signals and
liveness leases select the same backend. The implementation intentionally stops at the existing
``LeasePort`` contract; broader Postgres lease work remains issue #335.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql

from agentdeck.core.ports.lease import LeasePort
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection
    from datetime import timedelta

    type Connection = psycopg.AsyncConnection[tuple[Any, ...]]

_DEFAULT_SCHEMA = "agentdeck_control"


def _advisory_key(name: str) -> int:
    """A stable signed 64-bit advisory-lock key."""
    return int.from_bytes(
        hashlib.blake2b(name.encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )


class PostgresLeasePort(LeasePort):
    """One expiring lease row per run, using the Postgres server clock."""

    def __init__(self, dsn: str, *, schema: str = _DEFAULT_SCHEMA) -> None:
        self._dsn = dsn
        self._schema = schema
        self._conn: Connection | None = None
        self._lock = asyncio.Lock()
        self._setup_key = _advisory_key(f"agentdeck:control-state:setup:{schema}")

        table = sql.Identifier(schema, "leases")

        self._ddl = (
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(schema=sql.Identifier(schema)),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {table} ( run_id TEXT PRIMARY KEY, expires_at TIMESTAMPTZ NOT NULL)"
            ).format(table=table),
        )

        self._acquire = sql.SQL(
            "INSERT INTO {table} (run_id, expires_at) "
            "VALUES (%s, clock_timestamp() + (%s * interval '1 second')) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "expires_at = excluded.expires_at "
            "WHERE {table}.expires_at <= clock_timestamp() "
            "RETURNING run_id"
        ).format(table=table)

        self._renew = sql.SQL(
            "UPDATE {table} "
            "SET expires_at = clock_timestamp() + (%s * interval '1 second') "
            "WHERE run_id = %s "
            "RETURNING run_id"
        ).format(table=table)

        self._release = sql.SQL("DELETE FROM {table} WHERE run_id = %s").format(table=table)

        self._dead = sql.SQL(
            "SELECT run_id FROM {table} WHERE run_id = ANY(%s) AND expires_at <= clock_timestamp()"
        ).format(table=table)

    async def _ready(self) -> Connection:
        if self._conn is None:
            conn: Connection = await psycopg.AsyncConnection.connect(
                self._dsn,
                autocommit=True,
            )
            try:
                await conn.execute(
                    "SELECT pg_advisory_lock(%s)",
                    (self._setup_key,),
                )
                try:
                    for statement in self._ddl:
                        await conn.execute(statement)
                finally:
                    await conn.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (self._setup_key,),
                    )
            except BaseException:
                await conn.close()
                raise

            self._conn = conn

        return self._conn

    async def _run[T](
        self,
        work: Callable[[Connection], Awaitable[T]],
        op: str,
    ) -> T:
        async with self._lock:
            try:
                return await work(await self._ready())
            except psycopg.Error as exc:
                if self._conn is not None and self._conn.closed:
                    self._conn = None
                raise StoreError(f"run lease {op} failed: {exc}") from exc

    async def acquire(
        self,
        run_id: str,
        ttl: timedelta,
    ) -> bool:
        seconds = ttl.total_seconds()

        async def _work(conn: Connection) -> bool:
            cursor = await conn.execute(
                self._acquire,
                (run_id, seconds),
            )
            return await cursor.fetchone() is not None

        return await self._run(_work, "acquire")

    async def renew(
        self,
        run_id: str,
        ttl: timedelta,
    ) -> bool:
        seconds = ttl.total_seconds()

        async def _work(conn: Connection) -> bool:
            cursor = await conn.execute(
                self._renew,
                (seconds, run_id),
            )
            return await cursor.fetchone() is not None

        return await self._run(_work, "renew")

    async def release(self, run_id: str) -> None:
        async def _work(conn: Connection) -> None:
            await conn.execute(
                self._release,
                (run_id,),
            )

        await self._run(_work, "release")

    async def dead(
        self,
        run_ids: Collection[str],
    ) -> frozenset[str]:
        ids = tuple(run_ids)

        if not ids:
            return frozenset()

        async def _work(conn: Connection) -> frozenset[str]:
            cursor = await conn.execute(
                self._dead,
                (list(ids),),
            )
            return frozenset(row[0] for row in await cursor.fetchall())

        return await self._run(_work, "dead")

    async def aclose(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
        except psycopg.Error as exc:
            raise StoreError(f"closing the run leases failed: {exc}") from exc


__all__ = ["PostgresLeasePort"]
