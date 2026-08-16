"""In-process ``ControlPort``: a dict keyed by ``id``. Dev and single-process tests
only — process exit loses every pending signal, same posture as ``stores.memory``.
"""

from __future__ import annotations

from agentdeck.core.control import ControlSignal, Signal
from agentdeck.core.ports.control import ControlPort


class MemoryControlPort(ControlPort):
    """One signal per run, held in memory. Overwriting a run's signal is intentional: only
    the latest one matters, which is also how ``RESUME`` lifts a pending ``PAUSE``."""

    def __init__(self) -> None:
        self._signals: dict[str, ControlSignal] = {}

    async def signal(self, id: str, sig: Signal, reason: str | None = None) -> None:
        self._signals[id] = ControlSignal(verb=sig, reason=reason)

    async def poll(self, id: str) -> ControlSignal | None:
        return self._signals.get(id)

    async def consume(self, id: str, expected: Signal) -> bool:
        # Compare and delete under no await, so no other task can slip a signal in between the
        # two — the same "atomic for free" the in-memory event store's claims rely on.
        pending = self._signals.get(id)
        if pending is None or pending.verb is not expected:
            return False
        del self._signals[id]
        return True


__all__ = ["MemoryControlPort"]
