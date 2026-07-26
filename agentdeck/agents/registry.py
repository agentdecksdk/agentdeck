"""Auto-discovery of :class:`BaseAgent` subclasses in a project package."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.agents.base import BaseAgent
from agentdeck.runtime.registry import PluginRegistry


@dataclass(slots=True)
class AgentRegistry(PluginRegistry[BaseAgent]):
    """Discovers :class:`BaseAgent` subclasses in ``<package>/agents/<name>/agent.py``."""

    base_class: type[BaseAgent] = BaseAgent
    module_name: str = "agent"
    type_dir: str = "agents"
    label: str = "agent"


__all__ = ["AgentRegistry"]
