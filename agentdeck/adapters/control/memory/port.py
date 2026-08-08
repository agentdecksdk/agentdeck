"""In-process ``ControlPort``: a dict keyed by ``run_id``. Dev and single-process tests
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

    async def signal(self, run_id: str, sig: Signal, reason: str | None = None) -> None:
        self._signals[run_id] = ControlSignal(verb=sig, reason=reason)

    async def poll(self, run_id: str) -> ControlSignal | None:
        return self._signals.get(run_id)


__all__ = ["MemoryControlPort"]
