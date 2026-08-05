"""``ControlPort`` in SQLite: one row per ``run_id``, durable enough that a second OS
process — opening the same file, never sharing a connection or any Python state — can
signal a run it never held a reference to. This is what makes cross-process cancel real
instead of theoretical; Redis is the multi-worker upgrade, deferred to Story 3.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import TYPE_CHECKING

from agentdeck.core.ports.control import ControlPort, Signal

if TYPE_CHECKING:
    from pathlib import Path

# ponytail: the signals table grows one row per signaled run, never pruned — add a
# prune-on-terminal or TTL sweep when signal volume matters.
_SCHEMA = "CREATE TABLE IF NOT EXISTS signals (run_id TEXT PRIMARY KEY, signal TEXT NOT NULL);"


class SqliteControlPort(ControlPort):
    """One connection, serialized by a lock — same posture as ``stores.sqlite`` and for
    the same reason: ``sqlite3`` is stdlib but not coroutine-safe."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def signal(self, run_id: str, sig: Signal) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write, run_id, sig.value)

    async def poll(self, run_id: str) -> Signal | None:
        async with self._lock:
            row = await asyncio.to_thread(self._read, run_id)
        return Signal(row) if row is not None else None

    def _write(self, run_id: str, sig: str) -> None:
        self._conn.execute(
            "INSERT INTO signals (run_id, signal) VALUES (?, ?) ON CONFLICT(run_id) DO UPDATE SET signal = excluded.signal",
            (run_id, sig),
        )
        self._conn.commit()

    def _read(self, run_id: str) -> str | None:
        row = self._conn.execute("SELECT signal FROM signals WHERE run_id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()


__all__ = ["SqliteControlPort"]
