"""``NeverYields``: an ``EventStorePort`` wrapper that hands the loop no turn at all, for
tests that must prove a caller's liveness needs no help from the store (issue #87).

Every real store suspends somewhere (SQLite's own ``to_thread``); ``MemoryEventStore``
suspends once per ``append``, for fidelity with that. Wrapping either in ``NeverYields``
strips even that back out, so a liveness-sensitive caller  -  a bounded sink dispatch, a
drain  -  is tested against the one scheduling profile no real deployment provides but every
caller has to survive anyway: a store that never once lets another task run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentdeck.core.ports import EventStorePort, RunSummary, SessionClaim

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence
    from datetime import timedelta

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload, RunResumed, RunStarted
    from agentdeck.core.status import RunStatus


def _drive[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion by hand, resuming it past any bare scheduling yield
    (``await asyncio.sleep(0)``) without ever handing the turn to a real event loop.

    A bare yield carries no value and needs nothing external to become resumable, so
    stepping past it here is faithful to what the delegate actually does  -  the loop was
    never going to do more than immediately resume it either. Anything else yielded (a
    ``Future``, a real suspension on I/O) means the delegate needs the loop itself to make
    progress, which is exactly the dependency this wrapper exists to rule out: that is a
    store this wrapper cannot honor, not something to silently wait out.
    """
    while True:
        try:
            yielded = coro.send(None)
        except StopIteration as done:
            return done.value
        if yielded is not None:
            raise AssertionError(f"NeverYields cannot honor a delegate suspended on {yielded!r}")


class NeverYields(EventStorePort):
    """Delegates every call to a real store, but never once suspends doing it.

    Not a fake with its own state: the delegate's logic runs in full, including the
    scheduling yield ``MemoryEventStore.append`` now carries for fidelity  -  ``_drive`` just
    steps past it by hand. What is not honored is any real suspension: a delegate that
    genuinely needs the loop (SQLite's ``to_thread``) is not a fit for this wrapper and
    raises ``AssertionError`` rather than a silent hang.
    """

    def __init__(self, inner: EventStorePort) -> None:
        self._inner = inner

    async def append(self, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        return _drive(self._inner.append(payloads, ctx, origin))

    async def read_session(self, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        return _drive(self._inner.read_session(ctx, offset, limit))

    async def read_run(self, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        return _drive(self._inner.read_run(ctx, from_seq))

    async def claim_start(
        self,
        opening: RunStarted,
        ctx: RunContext,
        origin: str,
        stale_after: timedelta,
        *,
        dead: frozenset[str] = frozenset(),
    ) -> tuple[SessionClaim, Event | None]:
        return _drive(self._inner.claim_start(opening, ctx, origin, stale_after, dead=dead))

    async def claim_resume(self, resumed: RunResumed, ctx: RunContext, origin: str) -> Event | None:
        return _drive(self._inner.claim_resume(resumed, ctx, origin))

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        return _drive(self._inner.list_runs(ctx, status))

    async def find_by_key(self, ctx: RunContext, key: str) -> str | None:
        return _drive(self._inner.find_by_key(ctx, key))


__all__ = ["NeverYields"]
