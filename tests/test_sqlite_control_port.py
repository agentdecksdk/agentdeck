"""The SQLite ``ControlPort``: signals survive the process that wrote them, and the connection
posture that makes a second process's poll cheap is actually applied.

The signal round-trip is proven cross-process by ``tests/test_uc3_slowpoke.py`` and
``tests/concurrency_worker.py``; what those cannot show is the file's journal mode, because a
run behaves the same either way. That is what this file pins, so deleting the pragmas is a
failing test rather than an invisible regression.
"""

from __future__ import annotations

import sqlite3

import pytest

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.control.sqlite import port as port_module
from agentdeck.core.control import ControlSignal, Signal
from agentdeck.errors import StoreError


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
    the port has to work anyway  -  it is the mode every other test of it uses."""
    control = SqliteControlPort()
    assert control._conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    await control.signal("r-1", Signal.CANCEL, "user changed their mind")
    assert await control.poll("r-1") == ControlSignal(verb=Signal.CANCEL, reason="user changed their mind")
    assert await control.poll("r-2") is None


async def test_a_signal_replaces_the_one_pending_for_that_run_reason_included() -> None:
    """One row per run, latest write wins  -  which is how ``RESUME`` lifts a pause, and how a
    second cancel with a better reason overwrites the first rather than queueing behind it."""
    control = SqliteControlPort()
    await control.signal("r-1", Signal.PAUSE, "checking something")
    await control.signal("r-1", Signal.RESUME)
    assert await control.poll("r-1") == ControlSignal(verb=Signal.RESUME, reason=None)


async def test_a_control_db_written_before_signals_carried_a_reason_still_opens(tmp_path) -> None:
    """The pending-signal table gained a column, and a file from an earlier version does not
    have it: reading one is how the caller would find out, so opening adds it in place.

    Already on the ``id`` schema (the run_id-to-id migration is pinned on its own, below):
    this fixture isolates the *other* migration, the one that added ``reason``.
    """
    db_path = tmp_path / "old-control.sqlite3"
    old = sqlite3.connect(db_path)
    try:
        old.execute("CREATE TABLE signals (id TEXT PRIMARY KEY, signal TEXT NOT NULL)")
        old.execute("INSERT INTO signals (id, signal) VALUES ('r-old', 'cancel')")
        old.commit()
    finally:
        old.close()

    control = SqliteControlPort(db_path)
    try:
        assert await control.poll("r-old") == ControlSignal(verb=Signal.CANCEL, reason=None)
        await control.signal("r-old", Signal.PAUSE, "now with a reason")
        assert await control.poll("r-old") == ControlSignal(verb=Signal.PAUSE, reason="now with a reason")
    finally:
        control.close()


async def test_a_pre_id_control_db_with_no_pending_signal_migrates_silently(tmp_path) -> None:
    """The pre-namespace schema (``run_id`` primary key) never recorded a namespace at all, so
    an *empty* table carries nothing that could be misattributed  -  it is safe to carry forward
    as the new ``id``-keyed table, and a signal written afterwards is readable straight away."""
    db_path = tmp_path / "empty-run-id.sqlite3"
    old = sqlite3.connect(db_path)
    try:
        old.execute("CREATE TABLE signals (run_id TEXT PRIMARY KEY, signal TEXT NOT NULL, reason TEXT)")
        old.commit()
    finally:
        old.close()

    control = SqliteControlPort(db_path)
    try:
        assert await control.poll("some-id") is None
        await control.signal("some-id", Signal.CANCEL, "after the migration")
        assert await control.poll("some-id") == ControlSignal(verb=Signal.CANCEL, reason="after the migration")
    finally:
        control.close()


async def test_a_pre_id_control_db_with_a_pending_signal_refuses_to_open(tmp_path) -> None:
    """The pre-namespace schema stored every write under a bare ``run_id``, namespaced caller or
    not (that omission is exactly the defect #315 fixes)  -  so a row still pending at migration
    time cannot be trusted to have been unnamespaced. Re-keying it by identity could silently
    hand it to a different run than the one it was meant for, so opening refuses instead."""
    db_path = tmp_path / "pending-run-id.sqlite3"
    old = sqlite3.connect(db_path)
    try:
        old.execute("CREATE TABLE signals (run_id TEXT PRIMARY KEY, signal TEXT NOT NULL, reason TEXT)")
        old.execute("INSERT INTO signals (run_id, signal, reason) VALUES ('order-1234', 'cancel', NULL)")
        old.commit()
    finally:
        old.close()

    with pytest.raises(StoreError, match="pending control signal"):
        SqliteControlPort(db_path)
