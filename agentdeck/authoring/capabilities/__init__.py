"""Sandbox capability specs — declarative wrappers over SDK ``Capability`` types."""

from agentdeck.authoring.capabilities.compaction import CompactionSpec
from agentdeck.authoring.capabilities.filesystem import FilesystemSpec
from agentdeck.authoring.capabilities.memory import MemorySpec
from agentdeck.authoring.capabilities.shell import ShellSpec
from agentdeck.authoring.capabilities.spec import CapabilitiesSpec

__all__ = [
    "CapabilitiesSpec",
    "CompactionSpec",
    "FilesystemSpec",
    "MemorySpec",
    "ShellSpec",
]
