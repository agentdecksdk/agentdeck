"""``ControlPort`` in Postgres: one pending signal per canonical run ``id``.

The adapter is lazy: construction performs no network I/O, matching the Postgres event store.
Independent workers coordinate through Postgres itself. ``consume`` is a single conditional
``DELETE ... RETURNING`` so exactly one caller can take the signal it previously observed.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import sql

from agentdeck.core.control import ControlSignal, Signal
from agentdeck.core.ports.control import ControlPort
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    type Connection = psycopg.AsyncConnection[tuple[Any, ...]]

_DEFAULT_SCHEMA = "agentdeck_control"


def _advisory_key(name: str) -> int:
    """A stable signed 64-bit advisory-lock key."""
    return int.from_bytes(
        hashlib.blake2b(name.encode(), digest_size=8).digest(),
        "big",
        signed=True,
    )


class PostgresControlPort(ControlPort):
    """One signal row per run in a Postgres schema shared by every worker."""

    def __init__(self, dsn: str, *, schema: str = _DEFAULT_SCHEMA) -> None:
        self._dsn = dsn
        self._schema = schema
        self._conn: Connection | None = None
        self._lock = asyncio.Lock()
        self._setup_key = _advisory_key(f"agentdeck:control-state:setup:{schema}")

        table = sql.Identifier(schema, "signals")

        self._ddl = (
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(schema=sql.Identifier(schema)),
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {table} ( id TEXT PRIMARY KEY, signal TEXT NOT NULL, reason TEXT)"
            ).format(table=table),
        )

        self._write = sql.SQL(
            "INSERT INTO {table} (id, signal, reason) VALUES (%s, %s, %s) "
            "ON CONFLICT(id) DO UPDATE SET "
            "signal = excluded.signal, reason = excluded.reason"
        ).format(table=table)

        self._read = sql.SQL("SELECT signal, reason FROM {table} WHERE id = %s").format(table=table)

        self._consume = sql.SQL("DELETE FROM {table} WHERE id = %s AND signal = %s RETURNING id").format(table=table)

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
                raise StoreError(f"control signal {op} failed: {exc}") from exc

    async def signal(
        self,
        id: str,
        sig: Signal,
        reason: str | None = None,
    ) -> None:
        async def _work(conn: Connection) -> None:
            await conn.execute(
                self._write,
                (id, sig.value, reason),
            )

        await self._run(_work, "signal")

    async def poll(self, id: str) -> ControlSignal | None:
        async def _work(
            conn: Connection,
        ) -> tuple[str, str | None] | None:
            cursor = await conn.execute(
                self._read,
                (id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return row[0], row[1]

        row = await self._run(_work, "poll")

        if row is None:
            return None

        return ControlSignal(
            verb=Signal(row[0]),
            reason=row[1],
        )

    async def consume(
        self,
        id: str,
        expected: Signal,
    ) -> bool:
        async def _work(conn: Connection) -> bool:
            cursor = await conn.execute(
                self._consume,
                (id, expected.value),
            )
            return await cursor.fetchone() is not None

        return await self._run(_work, "consume")

    async def aclose(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
        except psycopg.Error as exc:
            raise StoreError(f"closing the control signals failed: {exc}") from exc


__all__ = ["PostgresControlPort"]
