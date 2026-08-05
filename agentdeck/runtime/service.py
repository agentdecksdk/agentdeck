"""The Runtime: the one place a run is orchestrated.

Per event, in this order: stamp the envelope, append to the log, fan out to sinks, yield.
The order is the contract — an event a consumer has seen is already persisted, so a
consumer that spots a ``seq`` gap can always refetch it.

Engines only yield payloads; ``seq``, ``tenant``, ``origin`` and ``ts`` are stamped here.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING

from agentdeck.core.events import (
    TERMINAL_KINDS,
    Event,
    RunCancelled,
    RunContextSnapshot,
    RunFailed,
    RunInterrupted,
    RunResumed,
    RunStarted,
)
from agentdeck.core.ports import Gate
from agentdeck.core.status import RunStatus
from agentdeck.errors import NotFoundError
from agentdeck.runtime.dispatch import SinkDispatch

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator, Mapping, Sequence
    from typing import Any

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import ControlPort, EnginePort, EventSinkPort, EventStorePort

logger = logging.getLogger(__name__)

# A run ending on one of these is waiting, not finished: its terminal event arrives on resume.
SUSPENDED_KINDS = frozenset({"run.interrupted", "run.paused"})


@dataclass(frozen=True, slots=True)
class PendingRun:
    """One run currently ``WAITING_HUMAN`` — what :meth:`Runtime.pending` lists."""

    run_id: str
    session_id: str | None
    invocable: str
    thread_id: str
    payload: dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


class Runtime:
    """Runs invocables and emits one canonical event stream, whatever engine did the work.

    Sinks are optional and buffered — each gets its own bounded queue, so the run is never
    pinned to one; ``clock`` is injected so tests need no wall clock.
    """

    def __init__(
        self,
        engines: Sequence[EnginePort],
        store: EventStorePort,
        invocables: Mapping[str, InvocableSpec],
        sinks: Sequence[EventSinkPort] = (),
        clock: Callable[[], datetime] = _now,
        control: ControlPort | None = None,
    ) -> None:
        self._engines = {engine.engine: engine for engine in engines}
        self._store = store
        self._invocables = invocables
        self._sinks = tuple(SinkDispatch(sink) for sink in sinks)
        self._clock = clock
        self._control = control

    async def run(self, name: str, input: Input, ctx: RunContext) -> AsyncGenerator[Event, None]:
        """Play one run of ``name``, yielding every event it produced, ``run.started`` first.

        The engine's exception, if any, reaches the caller — but ``run.failed`` is recorded
        first, so the log tells the whole story even when nobody was listening. Every exit
        closes the run in the log: a consumer that walks away gets ``run.cancelled``.
        """
        spec, engine = self._resolve(name)
        ctx = self._with_gate(ctx)
        # ponytail: whole log per run — window it (or hand the engine a summary) once a
        # session's history outgrows one read, which a real store will notice long before this does
        history = await self._store.read(ctx.log_key, ctx)
        seq = count()

        opening = RunStarted(
            invocable=spec.name,
            kind_of_invocable=spec.kind.value,
            parent_run_id=ctx.parent_run_id,
            input=input,
            context=RunContextSnapshot(
                principal=ctx.principal,
                trace_id=ctx.trace_id,
                budget=ctx.budget,
                triggered_by=ctx.triggered_by,
            ),
        )
        last = opening.kind

        try:
            yield await self._record(opening, spec, ctx, next(seq))
            async with aclosing(engine.start(spec, input, history, ctx)) as stream:
                async for payload in stream:
                    yield await self._record(payload, spec, ctx, next(seq))
                    last = payload.kind
                    if last in TERMINAL_KINDS:
                        # Terminal means terminal: stop reading so nothing can follow it into
                        # the log. An engine yielding more after this gets it discarded.
                        break
        except GeneratorExit:
            # Nobody is listening any more, so there is no event to yield — but an unclosed
            # run in the log is indistinguishable from one still in flight.
            logger.info("run %s abandoned by its consumer after %r", ctx.run_id, last)
            await self._record(RunCancelled(reason="consumer stopped reading"), spec, ctx, next(seq))
            raise
        except Exception as exc:
            # The exception is the caller's, the event is the record — both, always. The type
            # name only: an exception message can carry content that must not reach a sink.
            logger.exception("run %s failed in engine %r", ctx.run_id, engine.engine)
            failure = f"{type(exc).__name__} in engine {engine.engine!r}"
            yield await self._record(_engine_failed(failure), spec, ctx, next(seq))
            raise

        if last not in TERMINAL_KINDS and last not in SUSPENDED_KINDS:
            # An engine that just stops leaves consumers waiting forever; close the run for it.
            logger.error("engine %r ended run %s after %r, not a terminal event", engine.engine, ctx.run_id, last)
            yield await self._record(
                _engine_failed(f"engine {engine.engine!r} ended after {last!r}"), spec, ctx, next(seq)
            )

    async def resume(self, name: str, thread_id: str, value: Any, ctx: RunContext) -> AsyncGenerator[Event, None]:
        """Continue a run this Runtime suspended earlier.

        The store's conditional append makes the ``WAITING_HUMAN`` -> ``RUNNING`` transition
        atomic, so exactly one caller wins even when the callers are separate processes; the
        winner opens the run with ``run.resumed``, seq recovered from the log's own
        ``max(seq)`` so it stays contiguous across a process restart — never reset to 0.
        From there the engine plays on exactly like ``run()`` plays an opening: same
        terminal/suspended/exception handling.

        A stray resume — wrong status, already resumed by a racing caller, or a completed
        run — is a no-op: nothing is read from the engine, nothing is yielded.
        """
        spec, engine = self._resolve(name)
        ctx = self._with_gate(ctx)
        claimed = await self._claim_resume(spec, ctx)
        if claimed is None:
            return
        opening, seq = claimed
        yield opening
        last = opening.kind

        try:
            async with aclosing(engine.resume(spec, thread_id, value, ctx)) as stream:
                async for payload in stream:
                    yield await self._record(payload, spec, ctx, next(seq))
                    last = payload.kind
                    if last in TERMINAL_KINDS:
                        break
        except GeneratorExit:
            logger.info("run %s abandoned by its consumer after %r", ctx.run_id, last)
            await self._record(RunCancelled(reason="consumer stopped reading"), spec, ctx, next(seq))
            raise
        except Exception as exc:
            logger.exception("run %s failed in engine %r", ctx.run_id, engine.engine)
            failure = f"{type(exc).__name__} in engine {engine.engine!r}"
            yield await self._record(_engine_failed(failure), spec, ctx, next(seq))
            raise

        if last not in TERMINAL_KINDS and last not in SUSPENDED_KINDS:
            logger.error("engine %r ended run %s after %r, not a terminal event", engine.engine, ctx.run_id, last)
            yield await self._record(
                _engine_failed(f"engine {engine.engine!r} ended after {last!r}"), spec, ctx, next(seq)
            )

    async def _claim_resume(self, spec: InvocableSpec, ctx: RunContext) -> tuple[Event, Iterator[int]] | None:
        """Take the run's ``WAITING_HUMAN`` -> ``RUNNING`` transition, or ``None`` if someone
        else already has it.

        The store decides, in one conditional append: whoever's ``run.resumed`` lands is the
        one caller that gets to play the run on. That holds across processes, where a check
        followed by a separate append never could — two servers sharing a store would both
        read ``WAITING_HUMAN`` and both write. A loser reads nothing from the engine and
        yields nothing, so a stray resume stays a no-op rather than an error.

        ``seq`` comes from the log's own ``max(seq)``, so it continues across a process
        restart instead of resetting. It is read before the claim and can therefore go stale
        — the store refuses a claim whose ``seq`` is no longer the run's next one, so a
        caller that was slow enough to miss a whole resume-and-interrupt round of this run
        loses rather than reusing a ``seq`` somebody already wrote.
        """
        seq = count(await self._store.last_seq(ctx.log_key, ctx.run_id, ctx) + 1)
        event = self._stamp(RunResumed(reason=None), spec, ctx, next(seq))
        if not await self._store.claim_resume(ctx.log_key, ctx.run_id, event, ctx):
            return None
        await self._fan_out(event)
        return event, seq

    async def pending(self, ctx: RunContext) -> list[PendingRun]:
        """Every run currently ``WAITING_HUMAN`` for this tenant.

        Asks the store to project which runs are waiting rather than keeping an in-memory
        registry — a registry would go stale the moment a process restarted, which is
        exactly the bug this avoids. Only the matched runs get a (bounded, per-run) read,
        to pull the interrupt's ``thread_id`` and ``payload``.

        The listing and those reads are two snapshots, so a run can be resumed between them
        and come back already answered. That is harmless: the resume claim itself is what
        checks status, so acting on a stale entry is a no-op, not a double resume.
        """
        out: list[PendingRun] = []
        for summary in await self._store.list_runs(ctx, status=RunStatus.WAITING_HUMAN):
            found = _last_interrupt(await self._store.read_run(summary.log_key, summary.run_id, ctx))
            if found is None:
                continue
            event, interrupted = found
            out.append(
                PendingRun(
                    run_id=summary.run_id,
                    session_id=event.session_id,
                    invocable=event.origin,
                    thread_id=interrupted.thread_id or summary.run_id,
                    payload=interrupted.payload,
                )
            )
        return out

    async def drain(self) -> None:
        """Flush what the sinks have not taken yet, then stop their consumers.

        The composition root calls this at shutdown: without it, queued emits are destroyed
        with the event loop and the last few audit or cost events are silently lost. Never
        called per event — that would be exactly the join the fan-out exists to avoid.
        """
        await asyncio.gather(*(dispatch.drain() for dispatch in self._sinks), return_exceptions=True)

    def _with_gate(self, ctx: RunContext) -> RunContext:
        """Bind ``ctx.gate`` to this Runtime's ``ControlPort``, if it has one.

        The Runtime, not the caller, decides whether a run is cancellable — a caller
        builds a plain ``RunContext`` and never has to know a ``ControlPort`` exists.
        """
        if self._control is None:
            return ctx
        return replace(ctx, gate=Gate(self._control, ctx.run_id))

    def _resolve(self, name: str) -> tuple[InvocableSpec, EnginePort]:
        spec = self._invocables.get(name)
        if spec is None:
            raise NotFoundError(f"no invocable named {name!r}")
        engine = self._engines.get(spec.engine)
        if engine is None:
            raise NotFoundError(f"{name!r} needs engine {spec.engine!r}, which is not registered")
        return spec, engine

    async def _record(self, payload: KnownPayload, spec: InvocableSpec, ctx: RunContext, seq: int) -> Event:
        """Stamp, persist, fan out — in that order. Returns the event to yield."""
        event = self._stamp(payload, spec, ctx, seq)
        await self._store.append(ctx.log_key, [event], ctx)
        await self._fan_out(event)
        return event

    def _stamp(self, payload: KnownPayload, spec: InvocableSpec, ctx: RunContext, seq: int) -> Event:
        """The envelope an engine never sees. Split out of ``_record`` for the resume claim,
        which hands the store a finished event to append conditionally."""
        return Event(
            kind=payload.kind,
            seq=seq,
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            tenant=ctx.tenant,
            origin=spec.name,
            ts=self._clock(),
            payload=payload,
        )

    async def _fan_out(self, event: Event) -> None:
        """Sinks get a copy of the stream and no say in it: never called inline, never fatal.

        Each sink gets a queue put rather than an ``emit``, so this returns without
        suspending — unless a sink asked to be waited for when its queue is full, which only
        that sink's own ``BLOCK`` policy can do.
        """
        for dispatch in self._sinks:
            await dispatch.submit(event)


def _engine_failed(message: str) -> RunFailed:
    return RunFailed(error_code="engine_error", message=message, retryable=False)


def _last_interrupt(events: Sequence[Event]) -> tuple[Event, RunInterrupted] | None:
    for event in reversed(events):
        if isinstance(event.payload, RunInterrupted):
            return event, event.payload
    return None


__all__ = ["PendingRun", "SUSPENDED_KINDS", "Runtime"]
