"""The ports: small, role-shaped interfaces the outer rings implement.

Each is the narrowest thing its caller needs, so a surface that only reads events depends
on ``EventSinkPort`` and not on everything the Runtime can do.
"""

from agentdeck.core.ports.control import (
    ControlPort,
    ControlSignal,
    ControlSignalled,
    Gate,
    RunCancelledError,
    RunPausedError,
    Signal,
)
from agentdeck.core.ports.engine import EnginePort
from agentdeck.core.ports.sink import EventSinkPort
from agentdeck.core.ports.store import EventStorePort, RunSummary, SessionClaim
from agentdeck.core.ports.tools import ToolSet, ToolSourcePort

__all__ = [
    "ControlPort",
    "ControlSignal",
    "ControlSignalled",
    "EnginePort",
    "EventSinkPort",
    "EventStorePort",
    "Gate",
    "RunCancelledError",
    "RunPausedError",
    "RunSummary",
    "SessionClaim",
    "Signal",
    "ToolSet",
    "ToolSourcePort",
]
