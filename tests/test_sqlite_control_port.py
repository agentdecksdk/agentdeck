"""The SQLite ``ControlPort``: signals survive the process that wrote them, and the connection
posture that makes a second process's poll cheap is actually applied.

The signal round-trip is proven cross-process by ``tests/test_uc3_slowpoke.py`` and
``tests/concurrency_worker.py``; what those cannot show is the file's journal mode, because a
run behaves the same either way. That is what this file pins, so deleting the pragmas is a
failing test rather than an invisible regression.
"""

from __future__ import annotations

import sqlite3

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.control.sqlite import port as port_module
from agentdeck.core.ports.control import Signal


async def test_a_file_backed_control_db_opens_in_wal_with_the_busy_timeout_it_asked_for(tmp_path, monkeypatch) -> None:
    """WAL is what keeps a run's polling out of another process's signal write, and the timeout
    is what makes that write something to wait out rather than raise over. The mode is read back
    through a fresh connection, since it lives in the file's header rather than in this object.

    The timeout is patched to a value ``sqlite3`` would never choose on its own: the shipped one
    is also its default, so asserting that would pass whether the pragma ran or not.
    """
    monkeypatch.setattr(port_module, "_BUSY_TIMEOUT_MS", 3_000)
    db_path = tmp_path / "control.sqlite3"
    control = SqliteControlPort(db_path)
    try:
        await control.signal("r-1", Signal.CANCEL)
        assert control._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3_000
        assert (tmp_path / "control.sqlite3-wal").exists()
        peer = sqlite3.connect(db_path)
        try:
            assert peer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            peer.close()
    finally:
        control.close()


async def test_an_in_memory_control_db_still_works_where_there_is_no_wal_to_switch_to() -> None:
    """An in-memory database has no WAL mode; the pragma answers "memory" instead of failing, and
    the port has to work anyway — it is the mode every other test of it uses."""
    control = SqliteControlPort()
    assert control._conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    await control.signal("r-1", Signal.CANCEL)
    assert await control.poll("r-1") is Signal.CANCEL
    assert await control.poll("r-2") is None
