"""``ControlPort`` in SQLite: one row per ``run_id``, durable enough that a second OS
process — opening the same file, never sharing a connection or any Python state — can
signal a run it never held a reference to. This is what makes cross-process cancel real
instead of theoretical; Redis is the multi-worker upgrade, deferred to Story 3.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING

from agentdeck.core.control import ControlSignal, Signal
from agentdeck.core.ports.control import ControlPort
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ponytail: the signals table grows one row per signaled run, never pruned — add a
# prune-on-terminal or TTL sweep when signal volume matters.
_SCHEMA = "CREATE TABLE IF NOT EXISTS signals (run_id TEXT PRIMARY KEY, signal TEXT NOT NULL, reason TEXT);"

# A file written before signals carried a reason has no such column, and reading one is how
# the caller finds out. Added in place instead: a pending cancel is state worth keeping.
_ADD_REASON = "ALTER TABLE signals ADD COLUMN reason TEXT"

# Same as the event log's: long enough to wait a peer's write out, short enough that a wedged
# holder surfaces as an error rather than a hang.
_BUSY_TIMEOUT_MS = 5_000


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the signal table in WAL with an explicit busy timeout, so a run polling for a
    signal is not blocked by another process writing one, and a cancel is not refused because
    a poll happened to be reading.

    The timeout is set first so the mode switch can wait a peer's transaction out, and the
    switch is skipped when the file is already in WAL — re-asking is free there, but the
    conversion itself needs an exclusive lock that a peer's open write denies outright. A
    file that cannot be converted right now keeps the mode it has: slower under contention,
    never wrong. ``:memory:`` reports ``memory`` and stays there.
    """
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            # ponytail: silent, like the event store's — log it if an operator ever has to
            # find out why one process came up without WAL.
            with suppress(sqlite3.OperationalError):
                conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        if not any(row[1] == "reason" for row in conn.execute("PRAGMA table_info(signals)")):
            conn.execute(_ADD_REASON)
        conn.commit()
    except sqlite3.Error as exc:
        raise StoreError(f"cannot open the control signals at {db_path!r}: {exc}") from exc
    return conn


class SqliteControlPort(ControlPort):
    """One connection, serialized by a lock — same posture as ``stores.sqlite`` and for
    the same reason: ``sqlite3`` is stdlib but not coroutine-safe. Failures reach the caller
    as ``StoreError`` rather than as a ``sqlite3`` exception, and the same WAL caveats apply:
    ``-wal``/``-shm`` files sit beside this database, and it belongs on local disk because
    WAL is unreliable on network filesystems.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = _connect(str(db_path))
        self._lock = asyncio.Lock()

    async def _run[T](self, work: Callable[[], T], op: str) -> T:
        """Every statement goes through here — one caller at a time, off the event loop, and
        no library exception escaping the port."""
        async with self._lock:
            try:
                return await asyncio.to_thread(work)
            except sqlite3.Error as exc:
                raise StoreError(f"control signal {op} failed: {exc}") from exc

    async def signal(self, run_id: str, sig: Signal, reason: str | None = None) -> None:
        await self._run(partial(self._write, run_id, sig.value, reason), "signal")

    async def poll(self, run_id: str) -> ControlSignal | None:
        row = await self._run(partial(self._read, run_id), "poll")
        return ControlSignal(verb=Signal(row[0]), reason=row[1]) if row is not None else None

    async def consume(self, run_id: str, expected: Signal) -> bool:
        return await self._run(partial(self._take, run_id, expected.value), "consume")

    def _take(self, run_id: str, sig: str) -> bool:
        # One statement, so the comparison and the delete cannot be separated by a peer process's
        # write — which is the whole point of the port method being a compare-and-set.
        deleted = self._conn.execute("DELETE FROM signals WHERE run_id = ? AND signal = ?", (run_id, sig))
        self._conn.commit()
        return deleted.rowcount > 0

    def _write(self, run_id: str, sig: str, reason: str | None) -> None:
        self._conn.execute(
            "INSERT INTO signals (run_id, signal, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET signal = excluded.signal, reason = excluded.reason",
            (run_id, sig, reason),
        )
        self._conn.commit()

    def _read(self, run_id: str) -> tuple[str, str | None] | None:
        row = self._conn.execute("SELECT signal, reason FROM signals WHERE run_id = ?", (run_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StoreError(f"closing the control signals failed: {exc}") from exc


__all__ = ["SqliteControlPort"]
