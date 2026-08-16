"""The SQLite event log: same contract as ``tests/test_memory_store.py``, durable.

Mirrors that file's cases 1:1 for the append/read/read_run/tenancy basics, so a reviewer
can diff the two directly, plus the one thing memory cannot prove (durability across
instances). The focused queries — ``run_status``, ``list_runs``, paginated ``read`` — are
asserted once for both stores in ``tests/contract/test_store.py`` instead: SQLite answers
those with its own SQL rather than the port's default, so parity there is an invariant worth
running against every store, not prose to be diffed by hand. The last case here is the other
thing memory cannot prove: two connections to one file racing a resume claim, which is the
shape two server processes are in.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

import pytest

from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.adapters.stores.sqlite import store as store_module
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    KnownPayload,
    RunCompleted,
    RunInterrupted,
    RunResumed,
    RunStarted,
    TextDelta,
    Usage,
)
from agentdeck.errors import DuplicateKeyError

ORIGIN = "Greeter"


def _deltas(count: int) -> list[KnownPayload]:
    return [TextDelta(message_id="m1", text=f"chunk {which}") for which in range(count)]


def _ctx(namespace: str = "acme", run_id: str = "r-1") -> RunContext:
    return RunContext(namespace=namespace, run_id=run_id, session_id="s-1")


def _started() -> RunStarted:
    context = None
    return RunStarted(invocable="Approver", kind_of_invocable="workflow", input=[], context=context)


def _interrupted() -> RunInterrupted:
    return RunInterrupted(interrupt_id="i-1", reason="approval", payload={}, thread_id="t-1")


async def test_events_read_back_in_the_order_they_were_appended() -> None:
    store, ctx = SqliteEventStore(), _ctx()
    await store.append("s-1", _deltas(2), ctx, ORIGIN)
    await store.append("s-1", _deltas(1), ctx, ORIGIN)
    assert [event.seq for event in await store.read("s-1", ctx)] == [0, 1, 2]


async def test_from_seq_is_inclusive_so_zero_reads_the_whole_run() -> None:
    store, ctx = SqliteEventStore(), _ctx()
    await store.append("s-1", _deltas(3), ctx, ORIGIN)
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=0)] == [0, 1, 2]
    assert [event.seq for event in await store.read_run("s-1", "r-1", ctx, from_seq=2)] == [2]


async def test_a_seq_range_covers_one_run_and_never_splices_two() -> None:
    """``seq`` restarts at 0 per run, so a range over the whole log would return the tail of
    every run in it — which is why a range read has to name the run."""
    store, ctx = SqliteEventStore(), _ctx()
    await store.append("s-1", _deltas(2), ctx, ORIGIN)
    await store.append("s-1", _deltas(2), _ctx(run_id="r-2"), ORIGIN)

    tail = await store.read_run("s-1", "r-2", ctx, from_seq=1)
    assert [(event.run_id, event.seq) for event in tail] == [("r-2", 1)]
    assert [(event.run_id, event.seq) for event in await store.read("s-1", ctx)] == [
        ("r-1", 0),
        ("r-1", 1),
        ("r-2", 0),
        ("r-2", 1),
    ]


async def test_an_unknown_log_reads_as_empty() -> None:
    assert await SqliteEventStore().read("nobody", _ctx()) == []
    assert await SqliteEventStore().read_run("nobody", "r-1", _ctx()) == []


async def test_one_namespace_cannot_read_another_namespaces_log_under_the_same_key() -> None:
    """Two namespaces are free to pick the same session id; the store keeps them apart."""
    store = SqliteEventStore()
    await store.append("s-1", _deltas(1), _ctx("acme"), ORIGIN)
    await store.append("s-1", _deltas(1), _ctx("globex"), ORIGIN)

    acme = await store.read("s-1", _ctx("acme"))
    globex = await store.read("s-1", _ctx("globex"))
    assert [event.namespace for event in acme] == ["acme"]
    assert [event.namespace for event in globex] == ["globex"]


async def test_the_stub_completion_payload_round_trips_through_the_log() -> None:
    """The store holds events, not dicts — a payload comes back as the class it went in as."""
    store, ctx = SqliteEventStore(), _ctx()
    payload = RunCompleted(output=[TextBlock(text="done")], usage=Usage(input_tokens=1, output_tokens=1))
    await store.append("s-1", [payload], ctx, ORIGIN)
    assert (await store.read("s-1", ctx))[0].payload == payload


async def test_a_file_backed_log_opens_in_wal_with_the_busy_timeout_it_asked_for(tmp_path, monkeypatch) -> None:
    """WAL is what keeps a second process's reads out of this one's writes, and the timeout is
    what makes a peer's in-flight write something to wait out rather than raise over. The
    ``-wal`` side file is asserted too: it exists beside the database, which is a fact whoever
    copies or deletes that database has to know.

    The timeout is patched to a value ``sqlite3`` would never choose on its own, because the
    shipped one is also its default — asserting that would pass whether the pragma ran or not.
    """
    monkeypatch.setattr(store_module, "_BUSY_TIMEOUT_MS", 3_000)
    db_path = tmp_path / "events.sqlite3"
    store = SqliteEventStore(db_path)
    try:
        await store.append("s-1", _deltas(1), _ctx(), ORIGIN)
        # Per-connection, so only this store's own connection can be asked.
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 3_000
        assert (tmp_path / "events.sqlite3-wal").exists()
        peer = sqlite3.connect(db_path)  # persisted in the header: a fresh connection sees it
        try:
            assert peer.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            peer.close()
    finally:
        store.close()


async def test_opening_a_pre_wal_file_a_peer_is_writing_falls_back_instead_of_failing(tmp_path) -> None:
    """Converting a file *into* WAL takes an exclusive lock that a peer's open write denies
    outright — SQLite refuses it immediately, however long the busy timeout is. Opening a
    store there has to keep working in whatever mode the file is in: two processes starting
    at once on a fresh log must not turn a performance posture into a failure to open.
    """
    db_path = tmp_path / "events.sqlite3"
    SqliteEventStore(db_path).close()  # lay down the schema, so opening again writes nothing
    peer = sqlite3.connect(db_path)
    peer.execute("PRAGMA journal_mode = DELETE")
    peer.execute("BEGIN IMMEDIATE")
    peer.execute("INSERT INTO events (namespace, log_key, run_id, seq, data) VALUES ('acme', 's-1', 'r-9', 0, '{}')")
    try:
        store = SqliteEventStore(db_path)
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        store.close()
    finally:
        peer.rollback()
        peer.close()


async def test_an_in_memory_log_still_works_where_there_is_no_wal_to_switch_to() -> None:
    """An in-memory database has no WAL mode; the pragma answers "memory" instead of failing,
    and the store has to open and work anyway — it is the mode every other test here uses."""
    store = SqliteEventStore()
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    await store.append("s-1", _deltas(1), _ctx(), ORIGIN)
    assert [event.seq for event in await store.read("s-1", _ctx())] == [0]


async def test_persists_to_a_real_file_across_separate_store_instances(tmp_path) -> None:
    """The one thing memory can't prove: durability past the process holding it."""
    db_path = tmp_path / "events.sqlite3"
    ctx = _ctx()
    await SqliteEventStore(db_path).append("s-1", _deltas(2), ctx, ORIGIN)
    reopened = SqliteEventStore(db_path)
    assert [event.seq for event in await reopened.read("s-1", ctx)] == [0, 1]


async def test_the_run_id_index_migrates_onto_a_database_built_before_it_existed(tmp_path) -> None:
    """``events_by_run_id`` is new; a database an earlier build already created has the table
    but not this index. Unlike the ``UNIQUE`` index beside it, a plain one has nothing to
    conflict with, so opening the file adds it instead of refusing to open — asserted here
    rather than assumed, against a file this test builds by hand with the pre-``locate`` DDL.
    """
    db_path = tmp_path / "events.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, "
        "log_key TEXT NOT NULL, run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL);"
        "CREATE INDEX events_by_log ON events (namespace, log_key, id);"
        "CREATE UNIQUE INDEX events_by_run ON events (namespace, log_key, run_id, seq);"
    )
    conn.commit()
    conn.close()

    store = SqliteEventStore(db_path)
    try:
        names = {row[1] for row in store._conn.execute("PRAGMA index_list(events)").fetchall()}
        assert "events_by_run_id" in names
    finally:
        store.close()


def _pre_324_file(db_path) -> None:
    """A database exactly as an earlier build's ``_SCHEMA`` would have created it: no ``key``
    column, and ``events_by_run`` still scoped to ``(namespace, log_key, run_id, seq)`` — the
    shape #324 tightens to ``(namespace, run_id, seq)``."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL, "
        "log_key TEXT NOT NULL, run_id TEXT NOT NULL, seq INTEGER NOT NULL, data TEXT NOT NULL);"
        "CREATE INDEX events_by_log ON events (namespace, log_key, id);"
        "CREATE UNIQUE INDEX events_by_run ON events (namespace, log_key, run_id, seq);"
        "CREATE INDEX events_by_run_id ON events (namespace, run_id, id);"
    )
    conn.commit()
    conn.close()


async def test_a_pre_324_database_gains_the_key_column_and_the_tightened_index(tmp_path) -> None:
    """#324's migration, run against a file shaped exactly as an earlier build left it: opening
    it adds ``key`` (additive, nothing rewritten) and rebuilds ``events_by_run`` in the new,
    tighter shape rather than leaving the old one in place under ``IF NOT EXISTS``."""
    db_path = tmp_path / "events.sqlite3"
    _pre_324_file(db_path)

    store = SqliteEventStore(db_path)
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(events)").fetchall()}
        assert "key" in columns

        index_names = {row[1] for row in store._conn.execute("PRAGMA index_list(events)").fetchall()}
        assert "events_by_key" in index_names

        run_index_columns = [row[2] for row in store._conn.execute("PRAGMA index_info(events_by_run)").fetchall()]
        assert run_index_columns == ["namespace", "run_id", "seq"]
    finally:
        store.close()


async def test_a_migrated_database_still_enforces_the_tightened_run_index(tmp_path) -> None:
    """Not just present, load-bearing: a migrated file must refuse the exact corruption the
    tightened index exists to catch — one run_id+seq recorded under two different log keys —
    the same as a database this build created from scratch."""
    db_path = tmp_path / "events.sqlite3"
    _pre_324_file(db_path)
    store = SqliteEventStore(db_path)
    try:
        await store.append("s-1", _deltas(1), _ctx(), ORIGIN)
        # Raw SQL against the migrated index itself, deliberately bypassing the port's own
        # StoreError translation: same namespace, run_id and seq (0) as the write above, under a
        # different log_key — exactly the shape the old, looser index let through.
        with pytest.raises(sqlite3.IntegrityError, match="events.namespace, events.run_id, events.seq"):
            store._conn.execute(
                "INSERT INTO events (namespace, log_key, run_id, seq, data) VALUES ('acme', 's-2', 'r-1', 0, '{}')"
            )
    finally:
        store.close()


async def test_a_migrated_database_enforces_the_key_claim_too(tmp_path) -> None:
    """The migration is not merely schema-deep: a key claimed through a migrated file behaves
    exactly as one claimed through a database this build created fresh."""
    db_path = tmp_path / "events.sqlite3"
    _pre_324_file(db_path)
    store = SqliteEventStore(db_path)
    try:
        ctx = RunContext(namespace="acme", run_id="r-1", session_id="s-1", key="order-1234")
        claim, event = await store.claim_start("s-1", _started(), ctx, ORIGIN, timedelta(hours=1))
        assert event is not None

        other = RunContext(namespace="acme", run_id="r-2", session_id="s-2", key="order-1234")
        with pytest.raises(DuplicateKeyError):
            await store.claim_start("s-2", _started(), other, ORIGIN, timedelta(hours=1))
    finally:
        store.close()


async def test_two_connections_to_one_file_settle_a_resume_claim_on_one_winner(tmp_path) -> None:
    """Two stores over one file share no lock, no cache and no ``asyncio.Lock`` — the same
    position two server processes are in. Repeated, because a broken transaction boundary
    shows up as an occasional second winner rather than a consistent one.
    """
    for trial in range(20):
        db_path = tmp_path / f"race-{trial}.sqlite3"
        ctx = _ctx()
        seeder = SqliteEventStore(db_path)
        await seeder.append("s-1", [_started(), _interrupted()], ctx, "Approver")
        seeder.close()

        first, second = SqliteEventStore(db_path), SqliteEventStore(db_path)
        try:
            outcomes = await asyncio.gather(
                first.claim_resume("s-1", "r-1", RunResumed(reason=None), ctx, "Approver"),
                second.claim_resume("s-1", "r-1", RunResumed(reason=None), ctx, "Approver"),
            )
            assert [event is not None for event in outcomes].count(True) == 1, f"trial {trial}: {outcomes}"
            stored = await first.read_run("s-1", "r-1", ctx)
        finally:
            first.close()
            second.close()

        assert [event.kind for event in stored] == ["run.started", "run.interrupted", "run.resumed"]
        assert [event.seq for event in stored] == [0, 1, 2]
