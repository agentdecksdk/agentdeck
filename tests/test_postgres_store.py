"""The Postgres event log: the invariants only Postgres can be asked about.

Everything the four stores must agree on is asserted once, for all of them, in
``tests/contract/test_store.py``. What is left here is what this adapter alone can be wrong
about: no server at all, a peer holding the log's lock longer than this store will wait, a
server whose default isolation level would hand the claim a stale snapshot, and the schema
the log is supposed to keep to. The race cases use two store instances over one database  -
two connections sharing no lock, no cache and no ``asyncio.Lock``, which is the position two
server processes are in.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import live_stores
import pytest

from agentdeck.core.context import RunContext
from agentdeck.core.events import RunInterrupted, RunResumed, RunStarted
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from agentdeck.adapters.stores.postgres import PostgresEventStore

psycopg = live_stores.require_psycopg(module_level=True)

from agentdeck.adapters.stores.postgres import PostgresEventStore  # noqa: E402  -  needs psycopg above
from agentdeck.adapters.stores.postgres import store as store_module  # noqa: E402  -  needs psycopg above

ORIGIN = "Greeter"

# Windows nothing these cases write can fall inside or outside of, so staleness is decided by
# the argument and never by how long the test itself took.
NOTHING_IS_STALE = timedelta(hours=1)
EVERYTHING_IS_STALE = timedelta(0)

# Nothing listens on port 1, so every call fails at connect  -  the shape of a database gone
# unreachable mid-run, without waiting out a real timeout.
UNREACHABLE_DSN = "postgresql://postgres:postgres@127.0.0.1:1/nope"


def _ctx(namespace: str = "acme", run_id: str = "r-1", session_id: str = "s-1") -> RunContext:
    return RunContext(namespace=namespace, run_id=run_id, session_id=session_id)


def _started() -> RunStarted:
    return RunStarted(
        invocable=ORIGIN,
        kind_of_invocable="agent",
        input=[],
        context={"trace_id": "tr-1"},
    )


def _interrupted() -> RunInterrupted:
    return RunInterrupted(interrupt_id="i-1", reason="human", payload={"q": "ok?"}, thread_id="t-1")


@pytest.fixture
async def keyspace() -> AsyncIterator[tuple[str, str]]:
    async with live_stores.postgres_schema() as pair:
        yield pair


async def _warm(first: PostgresEventStore, second: PostgresEventStore, ctx: RunContext) -> None:
    """Connect and set both stores up *before* the race, so neither starts one behind.

    A cold store's first call connects, waits the schema-setup lock and runs four DDL
    statements. Gathering two cold ones races a lap against three, so the second peer arrives
    after the first has finished  -  the contention the test exists to create only sometimes
    happens. With both warm it happens every time.
    """
    await first.read_session(ctx)
    await second.read_session(ctx)


# Every method of the port, so one added later without the boundary wrapper is a missing case
# here rather than a psycopg exception surfacing to a caller that catches ``StoreError``.
_CALLS = [
    pytest.param(lambda store: store.append([_started()], _ctx(), ORIGIN), id="append"),
    pytest.param(lambda store: store.read_session(_ctx()), id="read"),
    pytest.param(lambda store: store.read_run(replace(_ctx(), run_id="r-1")), id="read_run"),
    pytest.param(lambda store: store.run_status(replace(_ctx(), run_id="r-1")), id="run_status"),
    pytest.param(lambda store: store.list_runs(_ctx()), id="list_runs"),
    pytest.param(lambda store: store.find_by_key(_ctx(), "order-1234"), id="find_by_key"),
    pytest.param(lambda store: store.claim_resume(RunResumed(reason=None), _ctx(), ORIGIN), id="claim_resume"),
    pytest.param(lambda store: store.claim_start(_started(), _ctx(), ORIGIN, NOTHING_IS_STALE), id="claim_start"),
]


@pytest.mark.parametrize("call", _CALLS)
async def test_a_server_that_cannot_be_reached_raises_a_store_error(
    call: Callable[[PostgresEventStore], Coroutine[Any, Any, object]],
) -> None:
    """A ``psycopg`` exception is a library type and must not cross a port.

    Both claims are what make this load-bearing: each promises a *refusal* as data, so an
    unreachable database has to be distinguishable from a peer that legitimately won. A
    fabricated refusal there would discard a human's approval, or open a second turn on a
    session that already has one, while reporting a race that never happened.
    """
    store = PostgresEventStore(UNREACHABLE_DSN)

    with pytest.raises(StoreError) as raised:
        await call(store)
    assert isinstance(raised.value.__cause__, psycopg.Error)
    assert not isinstance(raised.value, psycopg.Error)


async def test_two_connections_settle_a_resume_claim_on_one_winner(keyspace: tuple[str, str]) -> None:
    """Repeated, because a broken transaction boundary shows up as an occasional second
    winner rather than a consistent one."""
    dsn, schema = keyspace
    for trial in range(10):
        # A fresh run_id per trial, not just a fresh session_id: #324 tightened the run-scoped
        # unique index to (namespace, run_id, seq) with no session_id in it, so reusing one run_id
        # across trials would collide against the previous trial's own rows instead of testing
        # this trial's race.
        ctx = _ctx(run_id=f"r-{trial}", session_id=f"s-{trial}")
        seeder = PostgresEventStore(dsn, schema=schema)
        await seeder.append([_started(), _interrupted()], ctx, ORIGIN)
        await seeder.aclose()

        first, second = PostgresEventStore(dsn, schema=schema), PostgresEventStore(dsn, schema=schema)
        await _warm(first, second, ctx)
        try:
            outcomes = await asyncio.gather(
                first.claim_resume(RunResumed(reason=None), ctx, ORIGIN),
                second.claim_resume(RunResumed(reason=None), ctx, ORIGIN),
            )
            assert [event is not None for event in outcomes].count(True) == 1, f"trial {trial}: {outcomes}"
            stored = await first.read_run(ctx)
        finally:
            await first.aclose()
            await second.aclose()

        assert [event.kind for event in stored] == ["run.started", "run.interrupted", "run.resumed"]
        assert [event.seq for event in stored] == [0, 1, 2]


async def test_two_connections_settle_a_session_claim_on_one_winner(keyspace: tuple[str, str]) -> None:
    """The same race on the other claim: one session, two turns arriving together, and only
    one ``run.started`` may land in the log."""
    dsn, schema = keyspace
    for trial in range(10):
        # Fresh run_ids per trial for the same reason as the resume race above: #324's tightened
        # index has no session_id in it, so "r-a"/"r-b" reused across trials would collide with an
        # earlier trial's own rows rather than exercising this trial's race.
        ctx = _ctx(session_id=f"s-{trial}")
        first, second = PostgresEventStore(dsn, schema=schema), PostgresEventStore(dsn, schema=schema)
        await _warm(first, second, ctx)
        try:
            outcomes = await asyncio.gather(
                first.claim_start(_started(), replace(ctx, run_id=f"r-a-{trial}"), ORIGIN, NOTHING_IS_STALE),
                second.claim_start(_started(), replace(ctx, run_id=f"r-b-{trial}"), ORIGIN, NOTHING_IS_STALE),
            )
            stored = await first.read_session(ctx)
        finally:
            await first.aclose()
            await second.aclose()

        refused = [claim.held_by for claim, _ in outcomes if claim.held_by is not None]
        assert len(refused) == 1, f"trial {trial}: {outcomes}"
        # The log holds exactly the run the refusal named  -  the two answers cannot disagree.
        assert [event.run_id for event in stored] == [refused[0]], f"trial {trial}: {outcomes}"
        # And the winner was handed the event it wrote, while the loser was handed nothing.
        assert [event is not None for _, event in outcomes].count(True) == 1, f"trial {trial}: {outcomes}"


async def test_a_log_lock_held_past_the_timeout_is_a_store_error_not_a_lost_claim(
    keyspace: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wedged-peer failure in its own shape: somebody holds this log's lock longer than
    this store will wait for it.

    ``claim_resume`` must not answer that with ``None``. ``None`` means somebody else won
    and the resume is already recorded, so returning it here would throw away a human's
    approval while reporting a race that never happened. The timeout is shortened so the
    wait costs milliseconds.
    """
    monkeypatch.setattr(store_module, "_LOCK_TIMEOUT_MS", 50)
    dsn, schema = keyspace
    ctx = _ctx()
    store = PostgresEventStore(dsn, schema=schema)
    await store.append([_started(), _interrupted()], ctx, ORIGIN)

    peer = await psycopg.AsyncConnection.connect(dsn)
    try:
        await peer.execute("SELECT pg_advisory_xact_lock(%s)", (store_module._advisory_key("acme\x00s-1"),))
        with pytest.raises(StoreError) as raised:
            await store.claim_resume(RunResumed(reason=None), ctx, ORIGIN)
        assert isinstance(raised.value.__cause__, psycopg.errors.LockNotAvailable)
    finally:
        await peer.rollback()
        await peer.close()
        await store.aclose()


async def test_a_plain_append_cannot_commit_past_a_held_log_lock(
    keyspace: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The port's paging guarantee, on the one store that could break it.

    Row order is `BIGSERIAL`  -  assigned at insert, published at commit  -  so an append outside
    the log's lock can be given a *later* number than a claim's in-flight insert and still
    commit *first*. The claim's event then appears at an offset a reader has already gone
    past, so that event is never delivered and one of its neighbours is delivered twice. The
    fix is that a write is not allowed to commit while somebody holds this log's lock: with
    the lock held, an append waits and then fails, exactly as a claim does, and writes nothing.

    The same lock now also makes reading this run's last ``seq`` and inserting the next one
    indivisible, so an append that could commit past it would hand out a number twice.
    """
    monkeypatch.setattr(store_module, "_LOCK_TIMEOUT_MS", 50)
    dsn, schema = keyspace
    ctx = _ctx()
    store = PostgresEventStore(dsn, schema=schema)
    await store.read_session(ctx)  # connect and create the schema before the lock is taken

    peer = await psycopg.AsyncConnection.connect(dsn)
    try:
        await peer.execute("SELECT pg_advisory_xact_lock(%s)", (store_module._advisory_key("acme\x00s-1"),))
        with pytest.raises(StoreError) as raised:
            await store.append([_started()], ctx, ORIGIN)
        assert isinstance(raised.value.__cause__, psycopg.errors.LockNotAvailable)
    finally:
        await peer.rollback()
        await peer.close()

    assert await store.read_session(ctx) == []
    await store.aclose()


async def test_a_claim_still_reads_the_winners_rows_on_a_server_that_defaults_to_serializable(
    keyspace: tuple[str, str],
) -> None:
    """The claim pins ``READ COMMITTED`` rather than inheriting the server's default, and this
    is why: under a snapshot taken at the transaction's first statement, the loser's reads
    predate the winner's commit even though it waited for the lock  -  so it decides on a log
    that no longer exists and its insert fails the transaction instead of losing cleanly.
    """
    dsn, schema = keyspace
    strict = f"{dsn}{'&' if '?' in dsn else '?'}options=-c%20default_transaction_isolation%3Dserializable"
    ctx = _ctx()
    seeder = PostgresEventStore(strict, schema=schema)
    await seeder.append([_started(), _interrupted()], ctx, ORIGIN)
    await seeder.aclose()

    first, second = PostgresEventStore(strict, schema=schema), PostgresEventStore(strict, schema=schema)
    try:
        outcomes = await asyncio.gather(
            first.claim_resume(RunResumed(reason=None), ctx, ORIGIN),
            second.claim_resume(RunResumed(reason=None), ctx, ORIGIN),
        )
        assert [event is not None for event in outcomes].count(True) == 1, outcomes
        assert [event.seq for event in await first.read_run(replace(ctx, run_id="r-1"))] == [0, 1, 2]
    finally:
        await first.aclose()
        await second.aclose()


async def test_the_log_keeps_to_its_own_schema_and_leaves_the_rest_of_the_database_alone(
    keyspace: tuple[str, str],
) -> None:
    """ADR-D5's operational separation, as the one thing Postgres can enforce: a database that
    also holds the langgraph checkpointer's tables (which live in ``public``) shares nothing
    with the log, and two logs in two schemas share nothing with each other.
    """
    dsn, schema = keyspace
    ctx = _ctx()
    other_schema = f"{schema}_other"
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        mine = PostgresEventStore(dsn, schema=schema)
        theirs = PostgresEventStore(dsn, schema=other_schema)
        try:
            await mine.append([_started()], ctx, ORIGIN)
            await theirs.append([_started()], _ctx(run_id="r-9"), ORIGIN)

            assert [event.run_id for event in await mine.read_session(ctx)] == ["r-1"]
            assert [event.run_id for event in await theirs.read_session(ctx)] == ["r-9"]

            cursor = await admin.execute(
                "SELECT table_schema FROM information_schema.tables WHERE table_name = 'events'"
            )
            schemas = {row[0] for row in await cursor.fetchall()}
            assert schema in schemas
            assert "public" not in schemas
        finally:
            await mine.aclose()
            await theirs.aclose()
            await admin.execute(f'DROP SCHEMA IF EXISTS "{other_schema}" CASCADE')
    finally:
        await admin.close()


async def test_a_session_claim_that_wins_on_a_stale_run_reports_it_for_the_caller_to_close(
    keyspace: tuple[str, str],
) -> None:
    """Covered cross-store too, but asserted here against the real ``DISTINCT ON`` pairing of
    each open run with its own last event, which is where this store could get it wrong."""
    dsn, schema = keyspace
    store = PostgresEventStore(dsn, schema=schema)
    try:
        (abandoned,) = await store.append([_started()], _ctx(run_id="r-dead"), ORIGIN)
        claim, event = await store.claim_start(_started(), _ctx(run_id="r-new"), ORIGIN, EVERYTHING_IS_STALE)
        assert claim.held_by is None and event is not None
        # The abandoned run's own last event, not just its id: the caller needs that envelope to
        # write the closing event in that run's name.
        assert claim.overridden == (abandoned,)
    finally:
        await store.aclose()


async def test_opening_a_4x_events_table_fails_clearly_instead_of_migrating(keyspace: tuple[str, str]) -> None:
    """5.0 does not read a log 4.x wrote: opening a ``log_key``-shaped schema (confirmed against
    the v4.0.5 tag) raises a ``StoreError`` naming the fix, rather than silently rewriting it
    forward."""
    dsn, schema = keyspace
    admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(
            f'CREATE TABLE "{schema}".events (id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, '
            "log_key TEXT NOT NULL, run_id TEXT NOT NULL, key TEXT, seq INTEGER NOT NULL, "
            "data JSONB NOT NULL)"
        )

        with pytest.raises(StoreError, match="4.x"):
            await PostgresEventStore(dsn, schema=schema).read_session(_ctx())
    finally:
        await admin.close()
