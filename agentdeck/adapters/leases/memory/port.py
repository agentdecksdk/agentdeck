"""In-process ``LeasePort``: a dict of expiries keyed by ``run_id``.

Knows only what this process told it, which is the whole of its usefulness and the whole of
its limit: it witnesses a crashed *task* in this worker, never a crashed peer. Two processes
each get a port that has seen nothing of the other's runs, so neither takes anything over and
the staleness timer stays the only backstop  -  exactly today's behaviour, which is what makes
the default safe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentdeck.core.ports.lease import LeasePort

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import timedelta


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryLeasePort(LeasePort):
    """One expiry per run. ``clock`` is injectable for the same reason the memory store's is:
    a test that needs an expiry to have passed should not have to sleep through it."""

    def __init__(self, clock: Callable[[], datetime] = _now) -> None:
        self._expiries: dict[str, datetime] = {}
        self._clock = clock

    async def acquire(self, run_id: str, ttl: timedelta) -> bool:
        held = self._expiries.get(run_id)
        if held is not None and held > self._clock():
            return False
        self._expiries[run_id] = self._clock() + ttl
        return True

    async def renew(self, run_id: str, ttl: timedelta) -> bool:
        if run_id not in self._expiries:
            return False
        self._expiries[run_id] = self._clock() + ttl
        return True

    async def release(self, run_id: str) -> None:
        self._expiries.pop(run_id, None)

    async def dead(self, run_ids: Collection[str]) -> frozenset[str]:
        # `run_id in self._expiries` is the load-bearing half: a run this dict never held is
        # never reported, however long ago it was. Only an expiry this port itself wrote and
        # then watched pass counts as knowing the worker stopped.
        now = self._clock()
        return frozenset(
            run_id for run_id in run_ids if (expiry := self._expiries.get(run_id)) is not None and expiry <= now
        )


__all__ = ["MemoryLeasePort"]
