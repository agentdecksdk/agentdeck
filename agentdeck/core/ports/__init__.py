"""The ports: small, role-shaped interfaces the outer rings implement.

Each is the narrowest thing its caller needs, so a surface that only reads events depends
on ``EventSinkPort`` and not on everything the Runtime can do.
"""

from agentdeck.core.ports.control import ControlPort, Gate, RunCancelledError, Signal
from agentdeck.core.ports.engine import EnginePort
from agentdeck.core.ports.sink import EventSinkPort
from agentdeck.core.ports.store import EventStorePort, RunSummary
from agentdeck.core.ports.tools import ToolSet, ToolSourcePort

__all__ = [
    "ControlPort",
    "EnginePort",
    "EventSinkPort",
    "EventStorePort",
    "Gate",
    "RunCancelledError",
    "RunSummary",
    "Signal",
    "ToolSet",
    "ToolSourcePort",
]
