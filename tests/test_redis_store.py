"""The Redis event log: the invariants only Redis can be asked about.

Everything the four stores must agree on is asserted once, for all of them, in
``tests/contract/test_store.py``. What is left here is what this adapter alone can be wrong
about: no server at all, two clients racing the same claim through ``WATCH``/``MULTI``/``EXEC``,
the key escaping that keeps one namespace's ids from forging another's keys, and the prefix the
log is supposed to keep to. The race cases use two store instances over one server — two
clients sharing no lock, no cache and no ``asyncio.Lock``, which is the position two server
processes are in.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import live_stores
import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from agentdeck.adapters.stores.redis import RedisEventStore
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunInterrupted, RunResumed, RunStarted
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

ORIGIN = "Greeter"

# A window nothing these cases write can fall outside of, so no claim here reaches the
# staleness path — which the contract suite covers for every store.
NOTHING_IS_STALE = timedelta(hours=1)

# Nothing listens on port 1, so every call fails at connect — the shape of a server gone
# unreachable mid-run, without waiting out a real timeout.
UNREACHABLE_URL = "redis://127.0.0.1:1/0"


def _ctx(namespace: str = "acme", session_id: str = "s-1", run_id: str = "r-1") -> RunContext:
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
    async with live_stores.redis_keyspace() as pair:
        yield pair


# Every method of the port, so one added later without the boundary wrapper is a missing case
# here rather than a redis exception surfacing to a caller that catches ``StoreError``.
_CALLS = [
    pytest.param(lambda store: store.append("s-1", [_started()], _ctx(), ORIGIN), id="append"),
    pytest.param(lambda store: store.read("s-1", _ctx()), id="read"),
    pytest.param(lambda store: store.read_run("s-1", "r-1", _ctx()), id="read_run"),
    pytest.param(lambda store: store.run_status("s-1", "r-1", _ctx()), id="run_status"),
    pytest.param(lambda store: store.list_runs(_ctx()), id="list_runs"),
    pytest.param(lambda store: store.find_by_key(_ctx(), "order-1234"), id="find_by_key"),
    pytest.param(
        lambda store: store.claim_resume("s-1", "r-1", RunResumed(reason=None), _ctx(), ORIGIN), id="claim_resume"
    ),
    pytest.param(
        lambda store: store.claim_start("s-1", _started(), _ctx(), ORIGIN, NOTHING_IS_STALE), id="claim_start"
    ),
]


@pytest.mark.parametrize("call", _CALLS)
async def test_a_server_that_cannot_be_reached_raises_a_store_error(
    call: Callable[[RedisEventStore], Coroutine[Any, Any, object]],
) -> None:
    """A ``redis`` exception is a library type and must not cross a port.

    Both claims are what make this load-bearing: each promises a *refusal* as data, so an
    unreachable server has to be distinguishable from a peer that legitimately won. A
    fabricated refusal there would discard a human's approval, or open a second turn on a
    session that already has one, while reporting a race that never happened.
    """
    store = RedisEventStore(UNREACHABLE_URL)
    try:
        with pytest.raises(StoreError) as raised:
            await call(store)
        assert isinstance(raised.value.__cause__, RedisError)
        assert not isinstance(raised.value, RedisError)
    finally:
        await store.aclose()


async def test_two_clients_settle_a_resume_claim_on_one_winner(keyspace: tuple[str, str]) -> None:
    """Repeated, because a lost ``WATCH`` shows up as an occasional second winner rather than
    a consistent one."""
    url, prefix = keyspace
    ctx = _ctx()
    for trial in range(10):
        log_key = f"s-{trial}"
        seeder = RedisEventStore(url, prefix=prefix)
        await seeder.append(log_key, [_started(), _interrupted()], ctx, ORIGIN)
        await seeder.aclose()

        first, second = RedisEventStore(url, prefix=prefix), RedisEventStore(url, prefix=prefix)
        try:
            outcomes = await asyncio.gather(
                first.claim_resume(log_key, "r-1", RunResumed(reason=None), ctx, ORIGIN),
                second.claim_resume(log_key, "r-1", RunResumed(reason=None), ctx, ORIGIN),
            )
            assert [event is not None for event in outcomes].count(True) == 1, f"trial {trial}: {outcomes}"
            stored = await first.read_run(log_key, "r-1", ctx)
        finally:
            await first.aclose()
            await second.aclose()

        assert [event.kind for event in stored] == ["run.started", "run.interrupted", "run.resumed"]
        assert [event.seq for event in stored] == [0, 1, 2]


async def test_two_clients_settle_a_session_claim_on_one_winner(keyspace: tuple[str, str]) -> None:
    """The same race on the other claim: one session, two turns arriving together, and only
    one ``run.started`` may land in the log."""
    url, prefix = keyspace
    for trial in range(10):
        log_key = f"s-{trial}"
        first, second = RedisEventStore(url, prefix=prefix), RedisEventStore(url, prefix=prefix)
        try:
            outcomes = await asyncio.gather(
                first.claim_start(
                    log_key, _started(), _ctx(session_id=log_key, run_id="r-a"), ORIGIN, NOTHING_IS_STALE
                ),
                second.claim_start(
                    log_key, _started(), _ctx(session_id=log_key, run_id="r-b"), ORIGIN, NOTHING_IS_STALE
                ),
            )
            stored = await first.read(log_key, _ctx(session_id=log_key))
        finally:
            await first.aclose()
            await second.aclose()

        refused = [claim.held_by for claim, _ in outcomes if claim.held_by is not None]
        assert len(refused) == 1, f"trial {trial}: {outcomes}"
        # The log holds exactly the run the refusal named — the two answers cannot disagree.
        assert [event.run_id for event in stored] == [refused[0]], f"trial {trial}: {outcomes}"
        # And the winner was handed the event it wrote, while the loser was handed nothing.
        assert [event is not None for _, event in outcomes].count(True) == 1, f"trial {trial}: {outcomes}"


async def test_a_colon_in_a_namespace_cannot_reach_into_another_namespaces_log(keyspace: tuple[str, str]) -> None:
    """Keys are built by joining segments with ``:``, so an unescaped id containing one would
    make namespace ``"acme:x"`` + session ``"s"`` and namespace ``"acme"`` + session ``"x:s"`` the
    same key — two namespaces reading each other's runs through nothing but a chosen name.
    """
    url, prefix = keyspace
    store = RedisEventStore(url, prefix=prefix)
    try:
        outer = RunContext(namespace="acme:x", run_id="r-1", session_id="s", key="order-1234")
        inner = _ctx(namespace="acme", session_id="x:s")
        await store.claim_start("s", _started(), outer, ORIGIN, NOTHING_IS_STALE)

        assert [event.namespace for event in await store.read("s", outer)] == ["acme:x"]
        assert await store.read("x:s", inner) == []
        assert await store.read_run("x:s", "r-1", inner) == []
        assert await store.list_runs(inner) == []
        assert await store.find_by_key(inner, "order-1234") is None
    finally:
        await store.aclose()


async def test_the_log_keeps_to_its_prefix_and_leaves_the_engines_session_keys_alone(
    keyspace: tuple[str, str],
) -> None:
    """ADR-D5's operational separation, as the one thing Redis can enforce: an instance that
    also holds the openai-agents adapter's ``RedisSession`` conversations shares no key with
    the log, so either can be dropped without touching the other.
    """
    url, prefix = keyspace
    admin: Redis = Redis.from_url(url, decode_responses=True)
    store = RedisEventStore(url, prefix=prefix)
    try:
        await admin.set("agents:session:acme:s-1", "the engine's own state")
        # Diffed rather than listed absolutely: a dev's own server may hold anything else,
        # and what this asserts is that the log added nothing outside its prefix.
        before = {key async for key in admin.scan_iter(match="*")}
        await store.claim_start("s-1", _started(), _ctx(), ORIGIN, NOTHING_IS_STALE)
        await store.append("s-1", [_interrupted()], _ctx(), ORIGIN)
        added = {key async for key in admin.scan_iter(match="*")} - before

        assert added, "the log wrote nothing at all"
        assert {key for key in added if not key.startswith(f"{prefix}:")} == set()
        assert await admin.get("agents:session:acme:s-1") == "the engine's own state"
    finally:
        await admin.delete("agents:session:acme:s-1")
        await admin.aclose()
        await store.aclose()
