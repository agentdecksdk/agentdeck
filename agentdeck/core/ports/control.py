"""Cross-process run control: a signal addressed by ``run_id`` alone.

No ``RunContext`` on the port methods — ``run_id`` is globally unique, and a caller reaching for
a run it did not start (a second terminal, an operator's dashboard) has nothing else to offer.
Same reason the port carries ``reason``: the run's own loop records the request in the log, so
the words travel with the signal or are lost.

The transport only. What a signal means — the verbs, the safe point that notices one, the events
that record it being honored — is core's, in :mod:`agentdeck.core.control`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentdeck.core.control import ControlSignal, Signal


class ControlPort(ABC):
    """Write and read the pending signal for one run, from any process that knows its id."""

    @abstractmethod
    async def signal(self, run_id: str, sig: Signal, reason: str | None = None) -> None:
        """Record ``sig`` for ``run_id``, replacing whatever was pending. Idempotent.

        Signaling an ended run is harmless by construction, not by a check: nothing polls the gate
        once the run loop exits. ``RESUME`` lifts a pause rather than instructing a live run — it
        replaces the pending ``PAUSE`` so a resumed run does not stop at its first safe point.
        """

    @abstractmethod
    async def poll(self, run_id: str) -> ControlSignal | None:
        """The signal currently pending for ``run_id``, or ``None``."""
