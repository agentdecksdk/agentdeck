"""Sandbox capability specs — declarative wrappers over SDK ``Capability`` types."""

from agentdeck.agents.capabilities.compaction import CompactionSpec
from agentdeck.agents.capabilities.filesystem import FilesystemSpec
from agentdeck.agents.capabilities.memory import MemorySpec
from agentdeck.agents.capabilities.shell import ShellSpec
from agentdeck.agents.capabilities.spec import CapabilitiesSpec

__all__ = [
    "CapabilitiesSpec",
    "CompactionSpec",
    "FilesystemSpec",
    "MemorySpec",
    "ShellSpec",
]
