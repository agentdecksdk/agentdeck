"""Cross-process run control: a signal addressed by a run's ``id``.

No ``RunContext`` on the port methods, but an ``id`` in place of one: it is minted once per
run (never derived from ``namespace`` or a caller-supplied ``key``), so it is already globally
unique, and two namespaces can never share a signal row. That is what makes addressing a run
this port never opened (a second terminal, an operator's dashboard) possible without a
namespace ever reaching the transport. Same reason the port carries ``reason``: the run's own
loop records the request in the log, so the words travel with the signal or are lost.

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
    async def signal(self, id: str, sig: Signal, reason: str | None = None) -> None:
        """Record ``sig`` for ``id``, replacing whatever was pending. Idempotent.

        Signaling an ended run is harmless by construction, not by a check: nothing polls the gate
        once the run loop exits. ``RESUME`` lifts a pause rather than instructing a live run — it
        replaces the pending ``PAUSE`` so a resumed run does not stop at its first safe point.
        """

    @abstractmethod
    async def poll(self, id: str) -> ControlSignal | None:
        """The signal currently pending for ``id``, or ``None``."""

    @abstractmethod
    async def consume(self, id: str, expected: Signal) -> bool:
        """Clear ``id``'s pending signal if and only if it is still ``expected``. ``True``
        when this caller took it, ``False`` when somebody else's write got there first.

        The compare-and-set a honored signal needs, and the reason it is not a plain clear: an
        unconditional write would overwrite, and silently destroy, a cancel that arrived while
        the run was suspended — the one signal nothing else will ever notice. A caller that
        loses re-reads rather than acting on the intent it no longer holds.
        """
