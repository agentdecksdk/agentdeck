"""Aggregator spec — declares which sandbox capabilities an agent receives."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentdeck.authoring.capabilities.compaction import CompactionSpec
from agentdeck.authoring.capabilities.filesystem import FilesystemSpec
from agentdeck.authoring.capabilities.memory import MemorySpec
from agentdeck.authoring.capabilities.shell import ShellSpec
from agentdeck.authoring.capabilities.skills import build_skills

if TYPE_CHECKING:
    from agents.sandbox.capabilities import Capability


class CapabilitiesSpec(BaseModel):
    """Declares an agent's sandbox capabilities.

    Each structured capability accepts ``True`` (default spec) / ``False``
    (disabled) shorthand. Only :attr:`shell` defaults on.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    shell: ShellSpec | None = Field(default_factory=ShellSpec)
    skills: list[str] = Field(default_factory=list)
    skills_dir: Path | None = None
    compaction: CompactionSpec | None = None
    filesystem: FilesystemSpec | None = None
    memory: MemorySpec | None = None

    @field_validator("shell", "compaction", "filesystem", "memory", mode="before")
    @classmethod
    def _normalize_flag(cls, value: object) -> object:
        if value is True:
            return {}
        if value is False:
            return None
        return value

    def build(self, *, agent_model: str | None = None) -> list[Capability]:
        caps: list[Capability] = []
        if self.shell is not None:
            caps.append(self.shell.build())
        if self.skills:
            caps.append(build_skills(self.skills, self.skills_dir))
        if self.compaction is not None:
            caps.append(self.compaction.build(agent_model=agent_model))
        if self.filesystem is not None:
            caps.append(self.filesystem.build())
        if self.memory is not None:
            caps.append(self.memory.build())
        return caps


__all__ = ["CapabilitiesSpec"]
