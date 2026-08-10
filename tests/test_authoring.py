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


def test_standalone_build_rejects_a_bare_callable_tool():
    """``Agent.build()`` (no ``Deck``) shares ``compile_agent`` with ``Deck.build()`` (#172) —
    pinned here so the check can never move to a Deck-only validator without a red test."""
    agent = Agent(name="booking", instructions="x", tools=[lambda q: q])

    with pytest.raises(ConfigError, match="function_tool"):
        agent.build()
