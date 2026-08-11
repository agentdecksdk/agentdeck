"""``Agent``: the one construction API for an SDK agent, plus the declaration it can start from.

v1's ``BaseAgent`` (a class users subclassed) is renamed ``AgentDeclaration`` here — it is a
declarative *input* to ``Agent(...)``, never something invoked on its own (ruling 10,
plan-phase4-deck.md). ``Agent`` instances are immutable: a ``Deck`` compiles them once at
``build()``, and mutating one afterwards must have no effect on what was already compiled
(ruling 3) — immutability makes that true by construction rather than by convention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agents.agent_output import AgentOutputSchemaBase
    from agents.lifecycle import AgentHooks

_UNSET: Any = object()


class AgentDeclaration:
    """Reusable defaults for :class:`Agent`.

    Subclass and set class attributes to build a shareable base:

        class BookingBase(AgentDeclaration):
            instructions = "You handle bookings."

        booking = Agent(base=BookingBase, name="booking", tools=[...])

    Never constructed or run directly — it exists only to be named as ``Agent(base=...)``.
    """

    name: ClassVar[str | None] = None
    instructions: ClassVar[str] = ""
    handoff_description: ClassVar[str | None] = None
    model: ClassVar[str | None] = None
    model_settings: ClassVar[Mapping[str, Any]] = {}
    tools: ClassVar[Sequence[Any]] = ()
    handoffs: ClassVar[Sequence[Any]] = ()
    output_type: ClassVar[type[Any] | AgentOutputSchemaBase | None] = None
    hooks: ClassVar[AgentHooks | None] = None
    skills: ClassVar[Sequence[str]] = ()
    mcp: ClassVar[Sequence[str]] = ()


class Agent:
    """A declarative agent: name, instructions, tools, and the names of the skills/MCP
    servers/workflows it depends on — resolved against the owning :class:`~agentdeck.Deck`'s
    catalog at ``build()``, never at construction time, so an ``Agent`` built alone (with no
    ``Deck`` yet) never fails for referencing something it cannot see yet.

    ``base=`` is keyword-only by construction: every parameter here follows a bare ``*``, so
    ``Agent(SomeDeclaration, name=...)`` is a ``TypeError`` rather than a silently-accepted
    positional base. A value explicitly passed here always wins over ``base``'s, including an
    explicit empty value — omission, not falsiness, is what defers to the base.

    ``tools=`` takes plain functions — ``build()`` compiles each one, which is what lets a
    parameter annotated ``Context[...]`` be injected without ever appearing in the schema the
    model sees. An already-built Agents SDK tool object is still accepted and passed straight
    through as engine-native, introspected by nothing here. A callable whose signature cannot be
    read is refused at ``build()``, naming the agent and the callable.
    """

    __slots__ = (
        "name",
        "instructions",
        "handoff_description",
        "model",
        "model_settings",
        "tools",
        "handoffs",
        "output_type",
        "hooks",
        "skills",
        "mcp",
    )

    # Typed alongside `__slots__` so a type checker can see these as real attributes — the
    # values are set via `object.__setattr__` in `__init__`, which a bare `self.x = ...` a
    # checker infers attributes from would not need, but immutability (see `__setattr__`
    # below) does.
    name: str
    instructions: str
    handoff_description: str | None
    model: str | None
    model_settings: dict[str, Any]
    tools: tuple[Any, ...]
    handoffs: tuple[Any, ...]
    output_type: type[Any] | AgentOutputSchemaBase | None
    hooks: AgentHooks | None
    skills: tuple[str, ...]
    mcp: tuple[str, ...]

    def __init__(
        self,
        *,
        base: type[AgentDeclaration] | None = None,
        name: str = _UNSET,
        instructions: str = _UNSET,
        handoff_description: str | None = _UNSET,
        model: str | None = _UNSET,
        model_settings: Mapping[str, Any] = _UNSET,
        tools: Sequence[Any] = _UNSET,
        handoffs: Sequence[Any] = _UNSET,
        output_type: type[Any] | AgentOutputSchemaBase | None = _UNSET,
        hooks: AgentHooks | None = _UNSET,
        skills: Sequence[str] = _UNSET,
        mcp: Sequence[str] = _UNSET,
    ) -> None:
        source = base if base is not None else AgentDeclaration
        resolved_name = source.name if name is _UNSET else name
        if not resolved_name and base is not None:
            # A subclassed declaration that never set `name` names itself after its class,
            # the same convenience v1's `cls.name or cls.__name__` gave a `BaseAgent` subclass.
            resolved_name = base.__name__
        if not resolved_name:
            raise ValueError("Agent(name=...) is required (directly, or via base=).")
        object.__setattr__(self, "name", resolved_name)
        object.__setattr__(self, "instructions", source.instructions if instructions is _UNSET else instructions)
        object.__setattr__(
            self,
            "handoff_description",
            source.handoff_description if handoff_description is _UNSET else handoff_description,
        )
        object.__setattr__(self, "model", source.model if model is _UNSET else model)
        object.__setattr__(
            self, "model_settings", dict(source.model_settings if model_settings is _UNSET else model_settings)
        )
        object.__setattr__(self, "tools", tuple(source.tools if tools is _UNSET else tools))
        object.__setattr__(self, "handoffs", tuple(source.handoffs if handoffs is _UNSET else handoffs))
        object.__setattr__(self, "output_type", source.output_type if output_type is _UNSET else output_type)
        object.__setattr__(self, "hooks", source.hooks if hooks is _UNSET else hooks)
        object.__setattr__(self, "skills", tuple(source.skills if skills is _UNSET else skills))
        object.__setattr__(self, "mcp", tuple(source.mcp if mcp is _UNSET else mcp))

    def build(self) -> Any:
        """Compile to an SDK-native ``agents.Agent``, standalone (no catalog).

        Thin wrapper over :func:`agentdeck.authoring.compile.compile_agent` for the common
        case — an agent with no handoffs, or none needing a catalog to resolve. A ``Deck``
        calls ``compile_agent``/``link_handoffs`` directly instead, since it has the catalog.
        """
        from agentdeck.authoring.compile import compile_agent

        return compile_agent(self)

    async def run(self, message: Any = None, **runner_options: Any) -> Any:
        """One-shot headless run (no event log); returns the SDK ``RunResult``."""
        from agentdeck.authoring.runners.agent import HeadlessRunner

        return await HeadlessRunner.from_agent(self.build(), **runner_options).run(message)

    def __setattr__(self, key: str, value: Any) -> None:
        raise AttributeError(f"Agent is immutable; build a new one instead of setting {key!r}.")

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r})"


__all__ = ["Agent", "AgentDeclaration"]
