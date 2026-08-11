"""``Agent(base=Declaration, ...)``: override-on-construction, keyword only."""

from __future__ import annotations

import pytest

from agentdeck.authoring import Agent, AgentDeclaration
from agentdeck.errors import ConfigError


class BookingBase(AgentDeclaration):
    name = "booking"
    instructions = "You handle bookings."
    model = "gpt-4.1-mini"


def test_base_declaration_supplies_defaults():
    agent = Agent(base=BookingBase)

    assert agent.name == "booking"
    assert agent.instructions == "You handle bookings."
    assert agent.model == "gpt-4.1-mini"


def test_an_explicit_value_overrides_the_declarations_value():
    agent = Agent(base=BookingBase, instructions="You handle refunds.")

    assert agent.instructions == "You handle refunds."
    assert agent.name == "booking"  # untouched fields still come from the base


def test_an_explicit_empty_value_still_wins_over_the_base():
    """Omission, not falsiness, is what defers to ``base`` — an explicit ``tools=[]`` must
    not fall back to the base's own tools."""

    class WithTools(AgentDeclaration):
        name = "with-tools"
        tools = ("placeholder",)

    agent = Agent(base=WithTools, tools=[])

    assert agent.tools == ()


def test_a_positional_base_is_a_type_error():
    with pytest.raises(TypeError):
        Agent(BookingBase, name="booking")  # type: ignore[misc]


def test_agent_is_immutable_after_construction():
    agent = Agent(name="booking", instructions="You handle bookings.")

    with pytest.raises(AttributeError):
        agent.instructions = "changed"


def test_standalone_build_compiles_a_bare_callable_tool():
    """``Agent.build()`` (no ``Deck``) shares ``compile_agent`` with ``Deck.build()``, so a plain
    callable becomes a real SDK tool on both paths — pinned here so tool compilation can never
    move to a Deck-only step without a red test."""
    agent = Agent(name="booking", instructions="x", tools=[lambda q: q])

    (tool,) = agent.build().tools

    assert sorted(tool.params_json_schema["properties"]) == ["q"]


def test_standalone_build_rejects_a_tool_whose_signature_cannot_be_read():
    """The other half of the same seam: refusal is not a Deck-only check either."""

    def wrapper(*args, **kwargs): ...

    agent = Agent(name="booking", instructions="x", tools=[wrapper])

    with pytest.raises(ConfigError, match="signature could not be read"):
        agent.build()
