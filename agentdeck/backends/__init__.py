"""Backend layer — protocol shims that wrap catalog logic."""

from agentdeck.backends.base import BaseBackend
from agentdeck.backends.registry import BackendsRegistry

__all__ = ["BackendsRegistry", "BaseBackend"]
