"""In-process ``ControlPort``: a dict keyed by ``run_id``. Dev and single-process tests
only — process exit loses every pending signal, same posture as ``stores.memory``.
"""

from __future__ import annotations

from agentdeck.core.ports.control import ControlPort, Signal


class MemoryControlPort(ControlPort):
    """One signal per run, held in memory. Overwriting a run's signal is intentional:
    only the latest one matters, and M0 has exactly one (``CANCEL``) to overwrite with."""

    def __init__(self) -> None:
        self._signals: dict[str, Signal] = {}

    async def signal(self, run_id: str, sig: Signal) -> None:
        self._signals[run_id] = sig

    async def poll(self, run_id: str) -> Signal | None:
        return self._signals.get(run_id)


__all__ = ["MemoryControlPort"]
