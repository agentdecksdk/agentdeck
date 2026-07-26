"""Auto-discovery of :class:`BaseWorkflow` subclasses in a project package."""

from __future__ import annotations

from dataclasses import dataclass

from agentdeck.runtime.registry import PluginRegistry
from agentdeck.workflows.base import BaseWorkflow


@dataclass(slots=True)
class WorkflowRegistry(PluginRegistry[BaseWorkflow]):
    """Discovers :class:`BaseWorkflow` subclasses in ``<package>/<name>/workflow.py``."""

    base_class: type[BaseWorkflow] = BaseWorkflow
    module_name: str = "workflow"
    label: str = "workflow"


__all__ = ["WorkflowRegistry"]
