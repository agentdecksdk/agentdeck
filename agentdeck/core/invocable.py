"""What the Runtime can start.

An agent, a workflow and a skill differ only in ``kind`` and in what their engine makes of
``native``  -  so the Runtime has one code path, not one per shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from agentdeck.core.base import CoreModel

if TYPE_CHECKING:
    from collections.abc import Callable


class InvocableKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"
    SKILL = "skill"
    TOOL = "tool"


class InvocableSpec(CoreModel):
    """Engine-neutral description: the authoring layer compiles to this, engines read it.

    ``engine`` selects the adapter; ``native`` is that adapter's own payload and nothing
    outside it may look inside.

    Compiled, never parsed, so ``forbid``: an unknown keyword here is a typo.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: InvocableKind
    executor: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    native: Any = None


@dataclass(frozen=True, slots=True)
class AgentInstance:
    """One agent as something can be run: the name the log records, and the declaration behind it.

    What ``ctx.agent`` is, what ``ctx.agents.create()`` mints, and what ``ctx.invoke`` accepts
    beside a catalog name. The name is the whole of its address  -  a run resolves its invocable by
    name on every answer, resume and cancel  -  so an instance the catalog does not hold is one the
    deck registers under a minted name before anything can invoke it.

    ``declaration`` is the :class:`~agentdeck.authoring.agent.Agent` it plays, opaque here for
    :attr:`~agentdeck.core.context.RunContext.data`'s reason: core may not name an authoring type.
    Carried rather than looked up again, because forking one is copying exactly this.
    """

    name: str
    declaration: object


@runtime_checkable
class NativeInvocable(Protocol):
    """What the native executor needs of an AgentDeck-native definition.

    A protocol rather than the definition class itself, because that class is authoring's  -  it
    validates signatures and raises ``ConfigError``, neither of which belongs in core, and an
    executor may not import authoring (``.importlinter``). What crosses the boundary is this:
    five values and a callable, all of them settled at import.
    """

    name: str
    kind: InvocableKind
    call: Callable[..., Any]
    context_parameter: str | None
    """The parameter to inject a context into, or ``None`` if the body asked for none."""
    context_class: type | None
    """Which context it asked for: ``ToolCtx`` or ``WorkflowCtx``."""
    parameters: tuple[str, ...]
    """The body's own parameters, in order, context excluded  -  what an input binds to."""
