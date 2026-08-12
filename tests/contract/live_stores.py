"""One event store per backend for the contract suite, live services included.

The contract suite is the merge gate for store work, so a store that only answers correctly
in a mock is not evidence: Redis and Postgres get real servers here. CI runs them as service
containers on the gate job; locally they come from ``AGENTDECK_TEST_REDIS_URL`` /
``AGENTDECK_TEST_POSTGRES_DSN``, or the conventional local address if those are unset, and a
service that is not there skips with the variable's name in the reason rather than failing.

A silent skip is the failure mode that guard exists to catch: CI's skip-count ceiling turns
"the services quietly went away" into a red gate instead of a green one that proved nothing.

Every case gets its own keyspace — a fresh Redis prefix, a fresh Postgres schema — so tests
never see each other's events and never touch anything else living in a dev's local server.
``two_event_stores`` hands one case two handles on one such keyspace, for the promises a
single instance cannot be asked about; the keyspace context managers are exported too, for
the per-backend suites that build their own peers.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, NoReturn

import pytest

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.core.ports import EventStorePort

BACKENDS = ("memory", "sqlite", "redis", "postgres")

REDIS_URL_ENV = "AGENTDECK_TEST_REDIS_URL"
POSTGRES_DSN_ENV = "AGENTDECK_TEST_POSTGRES_DSN"

_DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/15"
_DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/agentdeck_test"


def require_psycopg(*, module_level: bool = False) -> Any:
    """Skip rather than error when psycopg is installed but its libpq is missing.

    Not `importorskip`: that skips a module which is absent and re-raises one which is present
    and broken — a deliberate distinction on its side, and `pip install psycopg` without the
    binary wheel lands squarely in the second case, where an error would take the whole
    collection down with it. Pass `module_level` when calling this while a module is importing.
    """
    try:
        import psycopg
    except ImportError as exc:
        pytest.skip(f"the Postgres event log needs psycopg with libpq: {exc}", allow_module_level=module_level)
    return psycopg


# Unique per case, and per process: a bare counter is only unique within the process that
# holds it, so two `make check` runs on one host (two worktrees, say) would hand out the same
# `agentdeck:test:0` against the same Redis and Postgres. Seeding the counter with the pid
# keeps two processes disjoint while keeping a failing case's keyspace greppable in output —
# `agentdeck:test:3f21-0`, not an unreadable uuid.
_run = f"{os.getpid():x}"
_names = count()

# Probing a dead port once per case would be ~70 pointless connection attempts, so the first
# refusal is remembered and every later case skips on the same reason.
_unavailable: dict[str, str] = {}


def redis_url() -> str:
    return os.environ.get(REDIS_URL_ENV) or _DEFAULT_REDIS_URL


def postgres_dsn() -> str:
    return os.environ.get(POSTGRES_DSN_ENV) or _DEFAULT_POSTGRES_DSN


@asynccontextmanager
async def event_store(backend: str) -> AsyncIterator[EventStorePort]:
    """One clean store for ``backend``, torn down afterwards."""
    if backend == "memory":
        yield MemoryEventStore()
    elif backend == "sqlite":
        sqlite = SqliteEventStore()
        try:
            yield sqlite
        finally:
            sqlite.close()
    elif backend == "redis":
        from agentdeck.adapters.stores.redis import RedisEventStore

        async with redis_keyspace() as (url, prefix):
            store = RedisEventStore(url, prefix=prefix)
            try:
                yield store
            finally:
                await store.aclose()
    elif backend == "postgres":
        require_psycopg()  # before the adapter import, which is what pulls libpq in
        from agentdeck.adapters.stores.postgres import PostgresEventStore

        async with postgres_schema() as (dsn, schema):
            postgres = PostgresEventStore(dsn, schema=schema)
            try:
                yield postgres
            finally:
                await postgres.aclose()
    else:
        raise ValueError(f"unknown store backend {backend!r}; expected one of {BACKENDS}")


@asynccontextmanager
async def two_event_stores(backend: str) -> AsyncIterator[tuple[EventStorePort, EventStorePort]]:
    """Two handles on one keyspace for ``backend``, torn down afterwards.

    The position two servers are in: two connections sharing no transaction, no cache and no
    ``asyncio.Lock``, so what they agree on they agree on through the store. Separate from
    ``event_store`` rather than layered on it because the shape differs per backend — SQLite
    needs a file, since two handles on ``:memory:`` get two unrelated databases.

    Memory yields one store twice, which is not a shortcut: its keyspace *is* the instance's
    dict and it holds nothing else, so two objects sharing that dict would be the same thing
    with more words. Two tasks on one instance is all "two writers" can mean in one process.
    """
    if backend == "memory":
        store = MemoryEventStore()
        yield store, store
    elif backend == "sqlite":
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            first, second = SqliteEventStore(path), SqliteEventStore(path)
            try:
                yield first, second
            finally:
                first.close()
                second.close()
    elif backend == "redis":
        from agentdeck.adapters.stores.redis import RedisEventStore

        async with redis_keyspace() as (url, prefix):
            redis_pair = (RedisEventStore(url, prefix=prefix), RedisEventStore(url, prefix=prefix))
            try:
                yield redis_pair
            finally:
                for redis_store in redis_pair:
                    await redis_store.aclose()
    elif backend == "postgres":
        require_psycopg()  # before the adapter import, which is what pulls libpq in
        from agentdeck.adapters.stores.postgres import PostgresEventStore

        async with postgres_schema() as (dsn, schema):
            postgres_pair = (PostgresEventStore(dsn, schema=schema), PostgresEventStore(dsn, schema=schema))
            try:
                yield postgres_pair
            finally:
                for postgres_store in postgres_pair:
                    await postgres_store.aclose()
    else:
        raise ValueError(f"unknown store backend {backend!r}; expected one of {BACKENDS}")


@asynccontextmanager
async def redis_keyspace() -> AsyncIterator[tuple[str, str]]:
    """A live Redis and a key prefix nothing else is using, emptied on the way out."""
    from redis.asyncio import Redis
    from redis.exceptions import RedisError

    url = redis_url()
    _skip_if_known_bad(url)
    # The harness keeps its own client: probing and cleaning up through the store under test
    # would make the fixture depend on the thing it is setting up.
    admin: Redis = Redis.from_url(url, decode_responses=True)
    try:
        try:
            await admin.ping()
        except RedisError as exc:
            _record_unavailable(url, f"no Redis at {url} ({exc}) — set {REDIS_URL_ENV} to point at one")
        prefix = f"agentdeck:test:{_run}-{next(_names)}"
        try:
            yield url, prefix
        finally:
            keys = [key async for key in admin.scan_iter(match=f"{prefix}:*")]
            if keys:
                await admin.delete(*keys)
    finally:
        await admin.aclose()


@asynccontextmanager
async def postgres_schema() -> AsyncIterator[tuple[str, str]]:
    """A live Postgres and a schema name nothing else is using, dropped on the way out."""
    psycopg = require_psycopg()

    dsn = postgres_dsn()
    _skip_if_known_bad(dsn)
    try:
        admin = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    except psycopg.Error as exc:
        _record_unavailable(dsn, f"no Postgres at {dsn} ({exc}) — set {POSTGRES_DSN_ENV} to point at one")
    schema = f"agentdeck_test_{_run}_{next(_names)}"
    try:
        yield dsn, schema
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


def _skip_if_known_bad(target: str) -> None:
    if target in _unavailable:
        pytest.skip(_unavailable[target])


def _record_unavailable(target: str, reason: str) -> NoReturn:
    _unavailable[target] = reason
    pytest.skip(reason)
