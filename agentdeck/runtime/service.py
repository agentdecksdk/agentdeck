"""The Runtime: the one place a run is orchestrated.

Per event, in this order: stamp the envelope, append to the log, fan out to sinks, yield.
The order is the contract — an event a consumer has seen is already persisted, so a
consumer that spots a ``seq`` gap can always refetch it.

Engines only yield payloads; ``seq``, ``tenant``, ``origin`` and ``ts`` are stamped here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING

from agentdeck.core.events import TERMINAL_KINDS, Event, RunContextSnapshot, RunFailed, RunStarted
from agentdeck.errors import NotFoundError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import KnownPayload
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import EnginePort, EventSinkPort, SessionStorePort

logger = logging.getLogger(__name__)

# A run ending on one of these is waiting, not finished: its terminal event arrives on resume.
SUSPENDED_KINDS = frozenset({"run.interrupted", "run.paused"})


def _now() -> datetime:
    return datetime.now(UTC)


class Runtime:
    """Runs invocables and emits one canonical event stream, whatever engine did the work.

    Sinks are optional and unordered; ``clock`` is injected so tests need no wall clock.
    """

    def __init__(
        self,
        engines: Sequence[EnginePort],
        store: SessionStorePort,
        invocables: Mapping[str, InvocableSpec],
        sinks: Sequence[EventSinkPort] = (),
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._engines = {engine.engine: engine for engine in engines}
        self._store = store
        self._invocables = invocables
        self._sinks = tuple(sinks)
        self._clock = clock
        # holds a reference to in-flight sink tasks so the loop can't collect them mid-emit
        # ponytail: unbounded set — a bounded queue per sink if one ever piles up
        self._sink_tasks: set[asyncio.Task[None]] = set()

    async def run(self, name: str, input: Input, ctx: RunContext) -> AsyncIterator[Event]:
        """Play one run of ``name``, yielding every event it produced, ``run.started`` first.

        The engine's exception, if any, reaches the caller — but ``run.failed`` is recorded
        first, so the log tells the whole story even when nobody was listening.
        """
        spec, engine = self._resolve(name)
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
        yield await self._record(opening, spec, ctx, next(seq))
        last = opening.kind

        try:
            async for payload in engine.start(spec, input, history, ctx):
                yield await self._record(payload, spec, ctx, next(seq))
                last = payload.kind
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
        event = Event(
            kind=payload.kind,
            seq=seq,
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            tenant=ctx.tenant,
            origin=spec.name,
            ts=self._clock(),
            payload=payload,
        )
        await self._store.append(ctx.log_key, [event], ctx)
        self._fan_out(event)
        return event

    def _fan_out(self, event: Event) -> None:
        """Sinks get a copy of the stream and no say in it: never awaited, never fatal."""
        for sink in self._sinks:
            task = asyncio.create_task(self._emit(sink, event))
            self._sink_tasks.add(task)
            task.add_done_callback(self._sink_tasks.discard)

    async def _emit(self, sink: EventSinkPort, event: Event) -> None:
        try:
            await sink.emit(event)
        except Exception:
            logger.exception("sink %s dropped %s seq=%d", type(sink).__name__, event.kind, event.seq)


def _engine_failed(message: str) -> RunFailed:
    return RunFailed(error_code="engine_error", message=message, retryable=False)


__all__ = ["SUSPENDED_KINDS", "Runtime"]
