"""Cross-process run control: a cancel signal addressed by ``run_id`` alone.

No ``RunContext`` parameter on the port methods — ``run_id`` is already globally unique
and the only signal M0 supports (``CANCEL``) needs no tenant/principal to act on. Pause,
resume and steering are Story 3; adding them later is additive to ``Signal``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum


class Signal(StrEnum):
    """The one control signal M0 supports."""

    CANCEL = "cancel"


class ControlPort(ABC):
    """Write and read the pending signal for one run, from any process that knows its id."""

    @abstractmethod
    async def signal(self, run_id: str, sig: Signal) -> None:
        """Record ``sig`` for ``run_id``. Idempotent: signaling twice, or signaling a run
        whose engine has already stopped polling, changes nothing further — the status
        machine (``core/status.py``) is what makes a signal on a terminal run a no-op, not
        this method re-deriving that rule."""

    @abstractmethod
    async def poll(self, run_id: str) -> Signal | None:
        """The signal currently pending for ``run_id``, or ``None``."""


class RunCancelledError(Exception):
    """Raised by :meth:`Gate.checkpoint` when the run's ``ControlPort`` carries ``CANCEL``.

    An engine adapter catches this at its own safe point and turns it into a
    ``run.cancelled`` event — it never reaches the Runtime or a caller as a bare exception.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id} was signaled CANCEL")
        self.run_id = run_id


class Gate:
    """One run's cooperative cancel point.

    With no ``control`` port (the default), ``checkpoint()`` is a no-op — every existing
    run that never wires a ``ControlPort`` behaves exactly as before. A run that does gets
    one bound to its own ``run_id``, built by the Runtime, never by the caller.
    """

    def __init__(self, control: ControlPort | None = None, run_id: str = "") -> None:
        self._control = control
        self._run_id = run_id

    async def checkpoint(self) -> None:
        """Return immediately, unless the run has been signaled CANCEL — then raise."""
        if self._control is None:
            return
        if await self._control.poll(self._run_id) is Signal.CANCEL:
            raise RunCancelledError(self._run_id)


__all__ = ["ControlPort", "Gate", "RunCancelledError", "Signal"]
