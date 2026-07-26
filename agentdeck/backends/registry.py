"""Auto-discovery of :class:`BaseBackend` subclasses in a project package."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.backends.base import BaseBackend
from agentdeck.runtime.registry import PluginRegistry


@dataclass(slots=True)
class BackendsRegistry(PluginRegistry[BaseBackend]):
    """Discovers :class:`BaseBackend` subclasses in ``<package>/<name>/backend.py``."""

    base_class: type[BaseBackend] = BaseBackend
    module_name: str = "backend"
    label: str = "backend"


__all__ = ["BackendsRegistry"]
