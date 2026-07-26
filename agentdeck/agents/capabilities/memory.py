"""Memory capability spec — pass-through dicts to SDK config dataclasses."""

from __future__ import annotations

from typing import Any

from agents.sandbox.capabilities import Memory
from agents.sandbox.config import MemoryGenerateConfig, MemoryLayoutConfig, MemoryReadConfig
from pydantic import BaseModel, ConfigDict, Field


class MemorySpec(BaseModel):
    """Memory capability spec.

    Each field maps 1:1 to an SDK config dataclass. ``read=None`` opts read
    config out entirely; the empty-dict default uses the SDK defaults.
    """

    model_config = ConfigDict(extra="forbid")

    layout: dict[str, Any] = Field(default_factory=dict)
    read: dict[str, Any] | None = Field(default_factory=dict)
    generate: dict[str, Any] | None = None

    def build(self) -> Memory:
        return Memory(
            layout=MemoryLayoutConfig(**self.layout),
            read=MemoryReadConfig(**self.read) if self.read is not None else None,
            generate=MemoryGenerateConfig(**self.generate) if self.generate is not None else None,
        )


__all__ = ["MemorySpec"]
