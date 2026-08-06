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
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from redis.asyncio import Redis
from redis.exceptions import RedisError, WatchError

from agentdeck.core.events import parse_event
from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, can_resume, status_of
from agentdeck.errors import StoreError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from datetime import datetime

    from redis.asyncio.client import Pipeline

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.core.status import RunStatus

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

    def __init__(self, url: str, *, prefix: str = _DEFAULT_PREFIX) -> None:
        # decode_responses because every value here is UTF-8 JSON this store wrote itself.
        self._client: Redis = Redis.from_url(url, decode_responses=True)
        self._prefix = prefix

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

    async def _refuse_a_taken_seq(self, pipe: Pipeline, tenant: str, log_key: str, events: Sequence[Event]) -> None:
        """One ``seq`` per run, ever — the durable stores' unique index, as a watched check.

        Watches each run's ``seq`` index as well as reading it, so a peer that spends one of
        these between the check and the write aborts the ``EXEC`` instead of doubling it. Every
        write goes through here, the conditional ones included: a store that guarded only the
        plain path would let a claim put two different events at one ``seq``, and nothing in the
        log would reveal it — ``check_contiguous`` looks for holes and a duplicate leaves none,
        and ``last_seq`` reads the same either way.

        Raised before anything is queued, so a refused batch leaves no half of itself behind.
        A ``StoreError`` and not a claim's refusal-as-data: a spent ``seq`` is corruption, not a
        race somebody lost.
        """
        await pipe.watch(*{self._seq_key(tenant, log_key, event.run_id) for event in events})
        taken: set[tuple[str, int]] = set()
        for event in events:
            spent = await pipe.zscore(self._seq_key(tenant, log_key, event.run_id), str(event.seq))
            if spent is not None or (event.run_id, event.seq) in taken:
                raise StoreError(f"seq {event.seq} of run {event.run_id!r} is already in log {log_key!r}")
            taken.add((event.run_id, event.seq))

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        """A plain append is a conditional one too: one ``seq`` per run, over the same
        ``WATCH``/``MULTI``/``EXEC`` the claims use (see ``_refuse_a_taken_seq``)."""
        foreign = {event.tenant for event in events} - {ctx.tenant}
        if foreign:
            raise ValueError(f"events for tenant(s) {sorted(foreign)} cannot be written to {ctx.tenant!r}'s log")
        if not events:
            return

        async def _attempt(pipe: Pipeline) -> None:
            await self._refuse_a_taken_seq(pipe, ctx.tenant, log_key, events)
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, events)
            await pipe.execute()

        await self._watched(_attempt, "append")

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
        return [parse_event(json.loads(row)) for row in rows]

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        try:
            rows = await self._client.lrange(self._run_key(ctx.tenant, log_key, run_id), 0, -1)
        except RedisError as exc:
            raise StoreError(f"event log read_run failed: {exc}") from exc
        events = [parse_event(json.loads(row)) for row in rows]
        return [event for event in events if event.seq >= from_seq]

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        try:
            scored = await self._client.zrange(
                self._seq_key(ctx.tenant, log_key, run_id), 0, 0, desc=True, withscores=True
            )
        except RedisError as exc:
            raise StoreError(f"event log last_seq failed: {exc}") from exc
        return int(scored[0][1]) if scored else -1

    async def claim_start(self, log_key: str, event: Event, ctx: RunContext, stale_before: datetime) -> SessionClaim:
        """The port's session claim over ``WATCH``/``MULTI``/``EXEC``: the log's set of runs
        and every open run's own keys are watched, so a peer opening a run under this
        decision aborts the write rather than doubling it.

        A refusal is data, as the port requires — the losing caller re-reads and names the
        run that actually holds the session. Only an unreachable store, a hopelessly contended
        one, or a ``seq`` this run has already spent raises.
        """
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")

        async def _attempt(pipe: Pipeline) -> SessionClaim:
            await pipe.watch(self._log_runs_key(ctx.tenant, log_key))
            run_ids = _sorted_text(await pipe.smembers(self._log_runs_key(ctx.tenant, log_key)))
            if run_ids:
                await pipe.watch(
                    *(self._life_key(ctx.tenant, log_key, run_id) for run_id in run_ids),
                    *(self._run_key(ctx.tenant, log_key, run_id) for run_id in run_ids),
                )
            overridden: list[str] = []
            for run_id in run_ids:
                life = await pipe.get(self._life_key(ctx.tenant, log_key, run_id))
                # A key the same MULTI filled in coming back empty means the keyspace lost it
                # — eviction, or an operator's DEL. Read as a run holding nothing rather than
                # crashing the claim, and note what that costs: a *live* run whose lifecycle
                # key went missing silently loses its session hold, and is not even reported
                # in `overridden` for the winner to close. Hence `noeviction` up top; there is
                # no answer a store can give here that is better than not evicting the record.
                if life is None or status_of([parse_event(json.loads(life))]) in TERMINAL_STATUSES:
                    continue
                last = await pipe.lindex(self._run_key(ctx.tenant, log_key, run_id), -1)
                if last is not None and parse_event(json.loads(last)).ts > stale_before:
                    return SessionClaim(held_by=run_id)
                overridden.append(run_id)
            await self._refuse_a_taken_seq(pipe, ctx.tenant, log_key, [event])
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, [event])
            await pipe.execute()
            return SessionClaim(overridden=tuple(overridden))

        return await self._watched(_attempt, "claim_start")

    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        """The port's conditional append over ``WATCH``/``MULTI``/``EXEC``: the run's status
        and its ``seq`` index are watched, so the write that publishes the
        ``WAITING_HUMAN`` -> ``RUNNING`` transition is the write that tested for it.

        A loser gets its clean ``False`` from what the winner wrote, never from a guess. An
        unreachable store raises instead, because it cannot know whether anybody resumed.
        """
        if event.run_id != run_id:
            raise ValueError(f"a claim on run {run_id!r} cannot carry an event for {event.run_id!r}")
        if event.tenant != ctx.tenant:
            raise ValueError(f"an event for tenant {event.tenant!r} cannot be written to {ctx.tenant!r}'s log")

        async def _attempt(pipe: Pipeline) -> bool:
            life_key = self._life_key(ctx.tenant, log_key, run_id)
            seq_key = self._seq_key(ctx.tenant, log_key, run_id)
            await pipe.watch(life_key, seq_key)
            life = await pipe.get(life_key)
            if not can_resume(status_of([parse_event(json.loads(life))] if life is not None else [])):
                return False
            scored = await pipe.zrange(seq_key, 0, 0, desc=True, withscores=True)
            if event.seq != (int(scored[0][1]) if scored else -1) + 1:
                # The run went round the loop while this claim was in flight: it waits
                # again, on a longer log, and this seq belongs to an event already written.
                return False
            pipe.multi()
            self._queue_writes(pipe, ctx.tenant, log_key, [event])
            await pipe.execute()
            return True

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
            RunSummary(log_key=log_key, run_id=run_id, status=status_of([parse_event(json.loads(life))]))
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
