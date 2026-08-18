"""What the Runtime can start.

An agent, a workflow and a skill differ only in ``kind`` and in what their engine makes of
``native``  -  so the Runtime has one code path, not one per shape.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field

from agentdeck.core.base import CoreModel


class InvocableKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"
    SKILL = "skill"


class InvocableSpec(CoreModel):
    """Engine-neutral description: the authoring layer compiles to this, engines read it.

    ``engine`` selects the adapter; ``native`` is that adapter's own payload and nothing
    outside it may look inside.

    Compiled, never parsed, so ``forbid``: an unknown keyword here is a typo.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: InvocableKind
    engine: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    native: Any = None
