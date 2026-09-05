"""Live Postgres tests for the minimal lease compatibility paired with issue #334."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

import live_stores
import pytest

from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

psycopg = live_stores.require_psycopg(module_level=True)

from agentdeck.adapters.leases.postgres import PostgresLeasePort  # noqa: E402

TTL = timedelta(seconds=60)
EXPIRED = timedelta(seconds=-1)
UNREACHABLE_DSN = "postgresql://postgres:postgres@127.0.0.1:1/nope"


@pytest.fixture
async def leases() -> AsyncIterator[tuple[PostgresLeasePort, PostgresLeasePort]]:
    async with live_stores.postgres_schema() as (dsn, schema):
        first = PostgresLeasePort(dsn, schema=schema)
        second = PostgresLeasePort(dsn, schema=schema)
        try:
            yield first, second
        finally:
            await first.aclose()
            await second.aclose()


async def test_a_run_the_backend_has_never_seen_is_not_dead(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, _ = leases

    assert await first.dead(["never-seen"]) == frozenset()


async def test_two_connections_see_the_same_live_and_expired_lease(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, second = leases

    assert await first.acquire("r-1", TTL) is True
    assert await second.dead(["r-1"]) == frozenset()

    assert await first.renew("r-1", EXPIRED) is True
    assert await second.dead(["r-1"]) == frozenset({"r-1"})


async def test_only_one_connection_acquires_a_live_run(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, second = leases

    outcomes = await asyncio.gather(
        first.acquire("r-1", TTL),
        second.acquire("r-1", TTL),
    )

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


async def test_an_expired_lease_can_be_taken_over(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, second = leases

    assert await first.acquire("r-1", EXPIRED) is True
    assert await second.dead(["r-1"]) == frozenset({"r-1"})
    assert await second.acquire("r-1", TTL) is True
    assert await first.dead(["r-1"]) == frozenset()


async def test_release_is_idempotent_and_forgets_the_lease(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, second = leases

    await first.acquire("r-1", EXPIRED)
    await second.release("r-1")
    await second.release("r-1")

    assert await first.dead(["r-1"]) == frozenset()


async def test_renewing_a_missing_lease_returns_false(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, _ = leases

    assert await first.renew("never-held", TTL) is False


async def test_asking_about_no_runs_returns_empty_without_querying_all_leases(
    leases: tuple[PostgresLeasePort, PostgresLeasePort],
) -> None:
    first, _ = leases

    assert await first.dead([]) == frozenset()


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda port: port.acquire("r-1", TTL),
            id="acquire",
        ),
        pytest.param(
            lambda port: port.renew("r-1", TTL),
            id="renew",
        ),
        pytest.param(
            lambda port: port.release("r-1"),
            id="release",
        ),
        pytest.param(
            lambda port: port.dead(["r-1"]),
            id="dead",
        ),
    ],
)
async def test_an_unreachable_server_raises_store_error(operation) -> None:
    port = PostgresLeasePort(UNREACHABLE_DSN)
    try:
        with pytest.raises(StoreError) as raised:
            await operation(port)
        assert isinstance(raised.value.__cause__, psycopg.Error)
        assert not isinstance(raised.value, psycopg.Error)
    finally:
        await port.aclose()
