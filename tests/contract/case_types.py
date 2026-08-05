"""The two types the contract-suite harness passes around — split out of ``contract_cases``
so per-engine case modules (e.g. ``openai_agents_cases``) can build ``Case``s without
importing the aggregate ``CASES`` list back (that would be circular).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from agentdeck.core.content import TextBlock

if TYPE_CHECKING:
    from agentdeck.core.content import Input
    from agentdeck.core.events import Event
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import EnginePort


@dataclass(frozen=True)
class Case:
    """One run to play. ``ends`` is ``"suspended"`` when the run is waiting on something, so
    its terminal event only arrives after a resume."""

    id: str
    engine: EnginePort
    spec: InvocableSpec
    ends: Literal["terminal", "suspended"]
    input: Input = field(default_factory=lambda: [TextBlock(text="any slot tuesday?")])


@dataclass(frozen=True)
class Played:
    """What one run produced: the events a consumer saw, and the error it ended with."""

    events: list[Event]
    error: Exception | None


__all__ = ["Case", "Played"]
