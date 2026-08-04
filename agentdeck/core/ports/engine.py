"""The engine boundary: start a run, yield payloads until it ends.

An engine yields payloads and nothing else — no envelopes, no ``seq``, no tenant — so
ordering and isolation stay with the Runtime and an engine cannot get them wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec


class EnginePort(ABC):
    """One execution engine.

    A run ends in exactly one of three ways: a terminal payload (``run.completed`` /
    ``run.failed`` / ``run.cancelled``), a suspending one (``run.interrupted`` /
    ``run.paused``) whose terminal event comes after resume, or a raised exception.
    Stopping after anything else is a contract violation the Runtime records as
    ``run.failed``.

    Engines emit existing kinds or namespaced ``custom`` — minting a kind is core's job.
    """

    engine: ClassVar[str]
    """Matches ``InvocableSpec.engine``; the Runtime selects the engine on it."""

    @abstractmethod
    def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncIterator[KnownPayload]:
        """Play one run. ``history`` is the log so far, which is the record of the session —
        an engine that keeps its own execution state loads that itself (ADR-D5).
        """


__all__ = ["EnginePort"]
