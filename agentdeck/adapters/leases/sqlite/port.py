"""``LeasePort`` in SQLite: one row per ``run_id``, in the file ``AGENTDECK_CONTROL`` names.

This is the backend that makes the issue's headline case real. A worker killed outright stops
renewing, its row expires, and the *next* worker — a different OS process, sharing only this
file — can positively assert the run is not being executed, instead of inferring it from an
hour of silence.

Same clock for everyone, taken from SQLite itself rather than each caller's Python: two workers
comparing their own clocks against a peer's expiry is how skew turns into a takeover of live
work. Same single-machine caveat as ``control.sqlite`` — WAL rests on shared memory, so one
file behind more than one machine is unsupported.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING

from agentdeck.core.ports.lease import LeasePort
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import timedelta
    from pathlib import Path

# ponytail: one row per leased run, deleted on release — a killed worker's row is the only
# kind that lingers, and the takeover that reads it releases it. No sweep needed until a
# fleet crashes faster than it recovers.
_SCHEMA = "CREATE TABLE IF NOT EXISTS leases (run_id TEXT PRIMARY KEY, expires_at TEXT NOT NULL);"

# Matches ``stores.sqlite``'s own ``_backend_now`` format: millisecond precision, explicit UTC
# offset, fixed width — so a plain string comparison in SQL is a chronological one.
_NOW = "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')"
# The bound parameter is a whole SQLite time modifier ("60.0 seconds"), not a bare number: a
# modifier assembled in SQL from `? || ' seconds'` yields NULL for anything SQLite will not
# parse, and a NOT NULL violation is a poor way to find out the TTL was malformed.
_EXPIRY = "strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', ?)"

_BUSY_TIMEOUT_MS = 5_000


def _modifier(ttl: timedelta) -> str:
    return f"{ttl.total_seconds()} seconds"


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the lease table in WAL with an explicit busy timeout, so a renewal is not blocked
    by a peer reading expiries and vice versa. Same posture, and same skip-if-already-WAL
    reasoning, as ``control.sqlite``."""
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            with suppress(sqlite3.OperationalError):
                conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    except sqlite3.Error as exc:
        raise StoreError(f"cannot open the run leases at {db_path!r}: {exc}") from exc
    return conn


class SqliteLeasePort(LeasePort):
    """One connection, serialized by a lock — ``sqlite3`` is stdlib but not coroutine-safe, so
    the same posture as every other SQLite-backed port here, failures included."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = _connect(str(db_path))
        self._lock = asyncio.Lock()

    async def _run[T](self, work: Callable[[], T], op: str) -> T:
        async with self._lock:
            try:
                return await asyncio.to_thread(work)
            except sqlite3.Error as exc:
                raise StoreError(f"run lease {op} failed: {exc}") from exc

    async def acquire(self, run_id: str, ttl: timedelta) -> bool:
        return await self._run(partial(self._acquire, run_id, _modifier(ttl)), "acquire")

    async def renew(self, run_id: str, ttl: timedelta) -> bool:
        return await self._run(partial(self._renew, run_id, _modifier(ttl)), "renew")

    async def release(self, run_id: str) -> None:
        await self._run(partial(self._release, run_id), "release")

    async def dead(self, run_ids: Collection[str]) -> frozenset[str]:
        ids = tuple(run_ids)
        if not ids:
            return frozenset()
        return await self._run(partial(self._dead, ids), "dead")

    def _acquire(self, run_id: str, modifier: str) -> bool:
        # One statement: the conflict is only overwritten when the existing lease has already
        # expired, so a live holder's row survives a peer's acquire without a read first.
        taken = self._conn.execute(
            f"INSERT INTO leases (run_id, expires_at) VALUES (?, {_EXPIRY}) "
            f"ON CONFLICT(run_id) DO UPDATE SET expires_at = excluded.expires_at "
            f"WHERE leases.expires_at <= {_NOW}",
            (run_id, modifier),
        )
        self._conn.commit()
        return taken.rowcount > 0

    def _renew(self, run_id: str, modifier: str) -> bool:
        renewed = self._conn.execute(f"UPDATE leases SET expires_at = {_EXPIRY} WHERE run_id = ?", (modifier, run_id))
        self._conn.commit()
        return renewed.rowcount > 0

    def _release(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM leases WHERE run_id = ?", (run_id,))
        self._conn.commit()

    def _dead(self, run_ids: tuple[str, ...]) -> frozenset[str]:
        # `run_id IN (...)` is what keeps this positive knowledge: a run with no row here
        # cannot come back from this query, whoever asks and whenever.
        placeholders = ",".join("?" * len(run_ids))
        rows = self._conn.execute(
            f"SELECT run_id FROM leases WHERE run_id IN ({placeholders}) AND expires_at <= {_NOW}", run_ids
        )
        return frozenset(row[0] for row in rows)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error as exc:
            raise StoreError(f"closing the run leases failed: {exc}") from exc


__all__ = ["SqliteLeasePort"]
