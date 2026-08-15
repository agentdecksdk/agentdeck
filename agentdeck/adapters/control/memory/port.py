"""In-process ``ControlPort``: a dict keyed by ``ref``. Dev and single-process tests
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

    async def signal(self, ref: str, sig: Signal, reason: str | None = None) -> None:
        self._signals[ref] = ControlSignal(verb=sig, reason=reason)

    async def poll(self, ref: str) -> ControlSignal | None:
        return self._signals.get(ref)

    async def consume(self, ref: str, expected: Signal) -> bool:
        # Compare and delete under no await, so no other task can slip a signal in between the
        # two — the same "atomic for free" the in-memory event store's claims rely on.
        pending = self._signals.get(ref)
        if pending is None or pending.verb is not expected:
            return False
        del self._signals[ref]
        return True


__all__ = ["MemoryControlPort"]
