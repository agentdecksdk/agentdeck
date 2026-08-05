"""``NeverYields``: an ``EventStorePort`` wrapper that hands the loop no turn at all, for
tests that must prove a caller's liveness needs no help from the store (issue #87).

Every real store suspends somewhere (SQLite's own ``to_thread``); ``MemoryEventStore``
suspends once per ``append``, for fidelity with that. Wrapping either in ``NeverYields``
strips even that back out, so a liveness-sensitive caller — a bounded sink dispatch, a
drain — is tested against the one scheduling profile no real deployment provides but every
caller has to survive anyway: a store that never once lets another task run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentdeck.core.ports import EventStorePort, RunSummary

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event
    from agentdeck.core.status import RunStatus


def _drive[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion by hand, resuming it past any bare scheduling yield
    (``await asyncio.sleep(0)``) without ever handing the turn to a real event loop.

    A bare yield carries no value and needs nothing external to become resumable, so
    stepping past it here is faithful to what the delegate actually does — the loop was
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
    scheduling yield ``MemoryEventStore.append`` now carries for fidelity — ``_drive`` just
    steps past it by hand. What is not honored is any real suspension: a delegate that
    genuinely needs the loop (SQLite's ``to_thread``) is not a fit for this wrapper and
    raises ``AssertionError`` rather than a silent hang.
    """

    def __init__(self, inner: EventStorePort) -> None:
        self._inner = inner

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        _drive(self._inner.append(log_key, events, ctx))

    async def read(self, log_key: str, ctx: RunContext, offset: int = 0, limit: int | None = None) -> list[Event]:
        return _drive(self._inner.read(log_key, ctx, offset, limit))

    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        return _drive(self._inner.read_run(log_key, run_id, ctx, from_seq))

    async def last_seq(self, log_key: str, run_id: str, ctx: RunContext) -> int:
        return _drive(self._inner.last_seq(log_key, run_id, ctx))

    async def claim_resume(self, log_key: str, run_id: str, event: Event, ctx: RunContext) -> bool:
        return _drive(self._inner.claim_resume(log_key, run_id, event, ctx))

    async def list_runs(self, ctx: RunContext, status: RunStatus | None = None) -> list[RunSummary]:
        return _drive(self._inner.list_runs(ctx, status))


__all__ = ["NeverYields"]
