"""Which runs a worker is positively known to have stopped executing.

Separate from :class:`~agentdeck.core.ports.control.ControlPort` because the two answer
different questions: a signal is a request arriving from outside a run, a lease is an
assertion made from inside it. Separate from the event store because the log is append-only
and durable while a lease is mutable and ephemeral — a lease that outlived its holder is
worthless, which is the opposite of what a log promises.

Addressed by the run's ``id`` alone, for the reason ``ControlPort`` gives: it is minted once
per run and never derived from ``namespace`` or a caller's ``key``, so it is already globally
unique and no namespace has to reach the transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import timedelta


class LeasePort(ABC):
    """A worker's periodic assertion that it is still executing a run, and the only question
    worth asking of it: which runs can this port say are **not** being executed.

    **Never reports absence of knowledge as death.** Liveness cannot be recorded across a
    process boundary, only witnessed, and a backend that never saw a run witnessed nothing
    about it. That is why :meth:`dead` is the verb rather than ``alive()``: an absent lease
    can then never cause a takeover, so a deployment whose backend knows nothing degrades
    exactly to the staleness timer instead of taking over every run it cannot see.
    """

    @abstractmethod
    async def acquire(self, run_id: str, ttl: timedelta) -> bool:
        """Assert that this worker is executing ``run_id`` for the next ``ttl``.

        ``False`` means somebody else's lease is live. A minted ``run_id`` is globally unique,
        so a genuine collision means two workers believe they are playing one run — worth
        reporting, never worth failing the turn over: the log's own conditional append is what
        actually admits one player.
        """

    @abstractmethod
    async def renew(self, run_id: str, ttl: timedelta) -> bool:
        """Push ``run_id``'s expiry out by ``ttl``. ``False`` when the lease is gone — expired
        under a stalled renewer, or released by something else."""

    @abstractmethod
    async def release(self, run_id: str) -> None:
        """Drop ``run_id``'s lease. Idempotent: releasing what was never held is not an error,
        because every exit path calls this and only some of them acquired anything."""

    @abstractmethod
    async def dead(self, run_ids: Collection[str]) -> frozenset[str]:
        """Which of ``run_ids`` this port **held and watched expire** — positive knowledge only.

        A run this port has never seen is never in the answer. Inverting that (``run_id not in
        table``) is the one-line mistake this port exists to prevent: a second process's memory
        lease knows nothing about the first's live runs, so "not present" would make every
        worker take over every other worker's work on sight.
        """


__all__ = ["LeasePort"]
