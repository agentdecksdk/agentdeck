"""The event log in Redis: the same contract as ``adapters.stores.sqlite``, shared by every
worker that can reach the instance.

Redis has no query planner to lean on, so the log carries its own indexes — a list per log
for append order, a list per run for a run's own slice, a sorted set of that run's ``seq``
numbers, the run's latest lifecycle event, and a set of the runs each log and each tenant
owns. Every write updates all of them inside one ``MULTI``/``EXEC``, so a reader never sees
an event in the log that is missing from its run's index. Status is still *derived* by
folding through ``core.status`` (ADR-D5: the log is the sole source of truth) — what the
indexes store is the last lifecycle **event**, never a status of their own.

Every key sits under one prefix (``agentdeck:events`` by default), which is how the
operational separation ADR-D5 asks for is expressed here: an instance that also holds the
openai-agents adapter's ``RedisSession`` conversations (``agents:session``) keeps the
platform record and the engine's private execution state in disjoint keyspaces, and either
can be dropped without touching the other.

**Operating notes.** ``append`` returning means Redis acknowledged the write, which is only
as durable as the instance is configured to be. The port promises an event a consumer has
seen is already in the store, so a deployment that uses this store as its record wants
``appendonly yes``; with the default snapshot-only persistence a crash can lose the last
seconds of a log. It also wants ``maxmemory-policy noeviction``: this is a record, not a
cache, and an evicted key does not merely disappear — a run whose latest lifecycle event was
evicted stops being seen as holding its session, so a live turn can have its session taken
from under it. Both settings belong to the whole instance, which is another reason to give
the log its own rather than share a cache's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from agentdeck.core.events import Event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, can_resume, status_of
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from datetime import timedelta

    from redis.asyncio.client import Pipeline

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload, RunResumed, RunStarted
    from agentdeck.core.status import RunStatus


def _now() -> datetime:
    return datetime.now(UTC)


_DEFAULT_PREFIX = "agentdeck:events"

# A claim re-reads and re-decides when a peer wrote to the same log under it. Bounded
# because an unbounded optimistic retry is a hang: past this many rounds the honest answer
# is that the store is too contended to settle, which is a store error and not a refusal.
_CLAIM_ATTEMPTS = 20


def _segment(value: str) -> str:
    """One key segment, escaped so ``:`` inside a tenant or session id cannot forge another.

    Without this, tenant ``"a:b"`` + log ``"c"`` and tenant ``"a"`` + log ``"b:c"`` are the
    same key, and two tenants read each other's runs — the isolation every store owes.
    """
    return quote(value, safe="")


class RedisEventStore(EventStorePort):
    """Append-only lists and their indexes under one Redis key prefix.

    Every write goes through ``WATCH``/``MULTI``/``EXEC`` — the two claims and the plain
    append, which is conditional on one ``seq`` per run: the keys the decision reads are
    watched, the decision itself runs here in Python — so ``core.status`` stays the one place
    a status is derived — and ``EXEC`` refuses to apply the write if any of those keys moved
    in between. A peer that got there first therefore never loses to a stale read; the write
    re-reads and answers from what the peer actually wrote. That is what makes this hold
    between two servers and not merely between two tasks.

    A ``redis`` exception never crosses the port: everything reaches the caller as
    ``StoreError``.
    """

    def __init__(self, url: str, *, prefix: str = _DEFAULT_PREFIX, clock: Callable[[], datetime] = _now) -> None:
        # decode_responses because every value here is UTF-8 JSON this store wrote itself.
        self._client: Redis = Redis.from_url(url, decode_responses=True)
        self._prefix = prefix
        # An injected callable rather than Redis's own TIME, per ADR-D11 §4: reading the server's
        # clock costs a round trip on the hot path, and unlike the SQL stores this one already
        # holds a WATCH across the read — the clock is not what its atomicity rests on.
        self._clock = clock

    def _log_key(self, tenant: str, log_key: str) -> str:
        return f"{self._prefix}:log:{_segment(tenant)}:{_segment(log_key)}"

    def _run_key(self, tenant: str, log_key: str, run_id: str) -> str:
        return f"{self._prefix}:run:{_segment(tenant)}:{_segment(log_key)}:{_segment(run_id)}"

    def _seq_key(self, tenant: str, log_key: str, run_id: str) -> str:
        return f"{self._prefix}:seq:{_segment(tenant)}:{_segment(log_key)}:{_segment(run_id)}"

    def _life_key(self, tenant: str, log_key: str, run_id: str) -> str:
        return f"{self._prefix}:life:{_segment(tenant)}:{_segment(log_key)}:{_segment(run_id)}"

    def _log_runs_key(self, tenant: str, log_key: str) -> str:
        return f"{self._prefix}:logruns:{_segment(tenant)}:{_segment(log_key)}"

    def _tenant_runs_key(self, tenant: str) -> str:
        return f"{self._prefix}:runs:{_segment(tenant)}"

    def _queue_writes(self, pipe: Pipeline, tenant: str, log_key: str, events: Iterable[Event]) -> None:
        """Buffer one batch's writes — the log, the run's slice and every index — onto a
        pipeline already in ``MULTI``, so no concurrent reader sees part of them.

        ``MULTI`` is atomic against *other clients*, not against a command of its own
        failing: Redis runs the queued commands back to back and reports per-command errors
        without rolling the rest back. Nothing here can fail on well-formed input — the keys
        are this store's own and each command matches the type it created — so the case that
        remains is a keyspace somebody else has written incompatible types into, which is
        unrecoverable by any means this store has.
        """
        for event in events:
            data = event.model_dump_json()
            pipe.rpush(self._log_key(tenant, log_key), data)
            pipe.rpush(self._run_key(tenant, log_key, event.run_id), data)
            pipe.zadd(self._seq_key(tenant, log_key, event.run_id), {str(event.seq): event.seq})
            if event.kind in LIFECYCLE_KINDS:
                pipe.set(self._life_key(tenant, log_key, event.run_id), data)
                pipe.sadd(self._log_runs_key(tenant, log_key), event.run_id)
                pipe.sadd(self._tenant_runs_key(tenant), _member(log_key, event.run_id))

    async def _stamp(
        self, pipe: Pipeline, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str
    ) -> list[Event]:
        """Read this run's next ``seq`` off its index and build the events, **watching** that
        index so a peer spending the same number between here and ``EXEC`` aborts this write
        instead of doubling it.

        That watch is the whole atomicity mechanism, and it replaces the explicit spent-``seq``
        check this store used to run: the number is no longer supplied by a caller who might
        reuse one, and a peer racing for it loses the ``EXEC`` rather than being refused. The
        index is a **ZSET** — ADR-D11 §6 originally prescribed ``INCR`` here, which would have
        returned ``WRONGTYPE`` on every append; #153 corrected it.

        Every payload in one call shares one ``ts``: a batch is one ``MULTI``, so it happened at
        one instant.
        """
        seq_key = self._seq_key(ctx.tenant, log_key, ctx.run_id)
        await pipe.watch(seq_key)
        scored = await pipe.zrange(seq_key, 0, 0, desc=True, withscores=True)
        seq = (int(scored[0][1]) if scored else -1) + 1
        now = self._clock()
        events = []
        for offset, payload in enumerate(payloads):
            events.append(
                Event(
                    kind=payload.kind,
                    seq=seq + offset,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    tenant=ctx.tenant,
                    origin=origin,
                    ts=now,
                    payload=payload,
                )
            )
        return events

    async def append(self, log_key: str, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        """A plain append is a conditional one too: the run's ``seq`` index is watched, read and
        extended over one ``WATCH``/``MULTI``/``EXEC``, exactly as the claims do."""
        if not payloads:
            return []

        async def _attempt(pipe: Pipeline) -> list[Event]:
            events = await self._stamp(pipe, log_key, payloads, ctx, origin)
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, events)
            await pipe.execute()
            return events

        return await self._watched(_attempt, "append")

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be None or >= 0, got {limit}")
        start = max(offset, 0)
        end = -1 if limit is None else start + limit - 1
        # A zero limit computes an end before the start, and LRANGE reads a negative index
        # from the tail — which for offset 0 would be the whole log rather than no page.
        if limit is not None and end < start:
            return []
        try:
            rows = await self._client.lrange(self._log_key(ctx.tenant, log_key), start, end)
        except RedisError as exc:
            raise StoreError(f"event log read failed: {exc}") from exc
        return [Event.model_validate(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        try:
            rows = await self._client.lrange(self._run_key(ctx.tenant, log_key, run_id), 0, -1)
        except RedisError as exc:
            raise StoreError(f"event log read_run failed: {exc}") from exc
        events = [Event.model_validate(json.loads(row)) for row in rows]
        return [event for event in events if event.seq >= from_seq]

    async def claim_start(
        self, log_key: str, opening: RunStarted, ctx: RunContext, origin: str, stale_after: timedelta
    ) -> tuple[SessionClaim, Event | None]:
        """The port's session claim over ``WATCH``/``MULTI``/``EXEC``: the log's set of runs
        and every open run's own keys are watched, so a peer opening a run under this
        decision aborts the write rather than doubling it.

        A refusal is data, as the port requires — the losing caller re-reads and names the
        run that actually holds the session. Only an unreachable store or a hopelessly
        contended one raises.
        """

        async def _attempt(pipe: Pipeline) -> tuple[SessionClaim, Event | None]:
            await pipe.watch(self._log_runs_key(ctx.tenant, log_key))
            run_ids = _sorted_text(await pipe.smembers(self._log_runs_key(ctx.tenant, log_key)))
            if run_ids:
                await pipe.watch(
                    *(self._life_key(ctx.tenant, log_key, run_id) for run_id in run_ids),
                    *(self._run_key(ctx.tenant, log_key, run_id) for run_id in run_ids),
                )
            stale_before = self._clock() - stale_after
            overridden: list[Event] = []
            for run_id in run_ids:
                life = await pipe.get(self._life_key(ctx.tenant, log_key, run_id))
                # A key the same MULTI filled in coming back empty means the keyspace lost it
                # — eviction, or an operator's DEL. Read as a run holding nothing rather than
                # crashing the claim, and note what that costs: a *live* run whose lifecycle
                # key went missing silently loses its session hold, and is not even reported
                # in `overridden` for the winner to close. Hence `noeviction` up top; there is
                # no answer a store can give here that is better than not evicting the record.
                if life is None or status_of([Event.model_validate(json.loads(life))]) in TERMINAL_STATUSES:
                    continue
                last = await pipe.lindex(self._run_key(ctx.tenant, log_key, run_id), -1)
                if last is None:
                    continue
                tail = Event.model_validate(json.loads(last))
                if tail.ts > stale_before:
                    return SessionClaim(held_by=run_id), None
                overridden.append(tail)
            events = await self._stamp(pipe, log_key, [opening], ctx, origin)
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, events)
            await pipe.execute()
            return SessionClaim(overridden=tuple(overridden)), events[0]

        return await self._watched(_attempt, "claim_start")

    async def claim_resume(
        self, log_key: str, run_id: str, resumed: RunResumed, ctx: RunContext, origin: str
    ) -> Event | None:
        """The port's conditional append over ``WATCH``/``MULTI``/``EXEC``: the run's status
        and its ``seq`` index are watched, so the write that publishes the
        ``WAITING_HUMAN`` -> ``RUNNING`` transition is the write that tested for it.

        A loser gets its clean ``None`` from what the winner wrote, never from a guess. An
        unreachable store raises instead, because it cannot know whether anybody resumed.
        """
        if ctx.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot be made in the context of {ctx.run_id!r}")

        async def _attempt(pipe: Pipeline) -> Event | None:
            life_key = self._life_key(ctx.tenant, log_key, run_id)
            await pipe.watch(life_key)
            life = await pipe.get(life_key)
            if not can_resume(status_of([Event.model_validate(json.loads(life))] if life is not None else [])):
                return None
            events = await self._stamp(pipe, log_key, [resumed], ctx, origin)
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, events)
            await pipe.execute()
            return events[0]

        return await self._watched(_attempt, "claim_resume")

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        """Overrides the port's per-run fold: the tenant's runs come off one set and each
        one's status off its stored last lifecycle event, so a listing deserializes one
        event per run instead of every event of every log."""
        try:
            members = _sorted_text(await self._client.smembers(self._tenant_runs_key(ctx.tenant)))
            runs = [_split(member) for member in members]
            async with self._client.pipeline(transaction=False) as pipe:
                for log_key, run_id in runs:
                    pipe.get(self._life_key(ctx.tenant, log_key, run_id))
                lifecycles = await pipe.execute()
        except RedisError as exc:
            raise StoreError(f"event log list_runs failed: {exc}") from exc
        summaries = [
            RunSummary(log_key=log_key, run_id=run_id, status=status_of([Event.model_validate(json.loads(life))]))
            for (log_key, run_id), life in zip(runs, lifecycles, strict=True)
            if life is not None
        ]
        return [summary for summary in summaries if status is None or summary.status is status]

    async def _watched[T](self, attempt: Callable[[Pipeline], Awaitable[T]], op: str) -> T:
        """Run one optimistic write until ``EXEC`` is not aborted by a concurrent one.

        Every write this store does goes through here — the two claims and the plain append,
        which is conditional on one ``seq`` per run. A fresh pipeline per round, so a round
        that lost its watch leaves nothing behind.
        """
        try:
            for _ in range(_CLAIM_ATTEMPTS):
                async with self._client.pipeline(transaction=True) as pipe:
                    try:
                        return await attempt(pipe)
                    except WatchError:
                        continue
        except RedisError as exc:
            raise StoreError(f"event log {op} failed: {exc}") from exc
        raise StoreError(f"event log {op} gave up after {_CLAIM_ATTEMPTS} contended attempts")

    async def aclose(self) -> None:
        try:
            await self._client.aclose()
        except RedisError as exc:
            raise StoreError(f"closing the event log failed: {exc}") from exc


def _sorted_text(members: Iterable[bytes | str]) -> list[str]:
    """One set's members as sorted text.

    ``redis`` types every reply as ``bytes | str`` because decoding is a client option
    rather than a protocol fact, and this client asks for text. Decoding the other branch
    anyway costs nothing and keeps the annotation honest. Sorted so that a log with two
    live runs names the same holder to every caller instead of whichever one the set
    happened to yield first.
    """
    return sorted(member.decode() if isinstance(member, bytes) else member for member in members)


def _member(log_key: str, run_id: str) -> str:
    return f"{_segment(log_key)}:{_segment(run_id)}"


def _split(member: str) -> tuple[str, str]:
    log_key, _, run_id = member.partition(":")
    return unquote(log_key), unquote(run_id)


__all__ = ["RedisEventStore"]
