"""Live Postgres tests for ``PostgresControlPort``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import live_stores
import pytest

from agentdeck.core.control import ControlSignal, Signal
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

psycopg = live_stores.require_psycopg(module_level=True)

from agentdeck.adapters.control.postgres import PostgresControlPort  # noqa: E402

UNREACHABLE_DSN = "postgresql://postgres:postgres@127.0.0.1:1/nope"


@pytest.fixture
async def controls() -> AsyncIterator[tuple[PostgresControlPort, PostgresControlPort]]:
    async with live_stores.postgres_schema() as (dsn, schema):
        first = PostgresControlPort(dsn, schema=schema)
        second = PostgresControlPort(dsn, schema=schema)
        try:
            yield first, second
        finally:
            await first.aclose()
            await second.aclose()


async def test_two_connections_share_signals_and_preserve_run_ids(
    controls: tuple[PostgresControlPort, PostgresControlPort],
) -> None:
    first, second = controls

    await first.signal("r-1", Signal.CANCEL, "stop")
    await first.signal("r-2", Signal.PAUSE, "wait")

    assert await second.poll("r-1") == ControlSignal(
        verb=Signal.CANCEL,
        reason="stop",
    )
    assert await second.poll("r-2") == ControlSignal(
        verb=Signal.PAUSE,
        reason="wait",
    )


async def test_signalling_again_overwrites_the_pending_signal(
    controls: tuple[PostgresControlPort, PostgresControlPort],
) -> None:
    first, second = controls

    await first.signal("r-1", Signal.PAUSE, "first")
    await second.signal("r-1", Signal.CANCEL, "replacement")

    assert await first.poll("r-1") == ControlSignal(
        verb=Signal.CANCEL,
        reason="replacement",
    )


async def test_only_one_of_two_connections_can_consume_the_same_signal(
    controls: tuple[PostgresControlPort, PostgresControlPort],
) -> None:
    first, second = controls

    await first.signal("r-1", Signal.CANCEL)

    outcomes = await asyncio.gather(
        first.consume("r-1", Signal.CANCEL),
        second.consume("r-1", Signal.CANCEL),
    )

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    assert await first.poll("r-1") is None


async def test_consuming_the_wrong_signal_does_not_delete_the_current_one(
    controls: tuple[PostgresControlPort, PostgresControlPort],
) -> None:
    first, second = controls

    await first.signal("r-1", Signal.CANCEL, "keep me")

    assert await second.consume("r-1", Signal.PAUSE) is False
    assert await first.poll("r-1") == ControlSignal(
        verb=Signal.CANCEL,
        reason="keep me",
    )


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            lambda port: port.signal("r-1", Signal.CANCEL),
            id="signal",
        ),
        pytest.param(
            lambda port: port.poll("r-1"),
            id="poll",
        ),
        pytest.param(
            lambda port: port.consume("r-1", Signal.CANCEL),
            id="consume",
        ),
    ],
)
async def test_an_unreachable_server_raises_store_error(operation) -> None:
    port = PostgresControlPort(UNREACHABLE_DSN)
    try:
        with pytest.raises(StoreError) as raised:
            await operation(port)
        assert isinstance(raised.value.__cause__, psycopg.Error)
        assert not isinstance(raised.value, psycopg.Error)
    finally:
        await port.aclose()
