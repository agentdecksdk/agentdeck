"""The request shape a handoff produces, on both sides of `AGENTDECK_RUNNER_HANDOFF_ENDS_ON_USER_TURN`,
and what `AGENTDECK_RUNNER_HANDOFF_CLOSING_TURN` puts in the turn it appends.

`nest_handoff_history` (always on, `composition.resolve_run_settings`) collapses a handoff's
transcript into a single assistant message — the transferred-to agent's request ends on that
role, which some OpenAI-compatible endpoints reject outright. This is the probe that shape:
what the transferred-to agent's model call actually receives, via `scripted_model_server`
(the same harness `tests/test_handoff_round_trip.py` drives a handoff cycle through), never
against a real non-OpenAI endpoint — whether a given provider rejects a shape is that
provider's documented behavior, not something this suite reproduces.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentdeck.authoring import Agent
from agentdeck.deck import Deck
from agentdeck.runtime.settings import get_settings
from agentdeck.testing import scripted_model_server


@pytest.fixture
def no_project(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _settings_cache() -> Any:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _wire_openai(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    for name, value in {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": "handoff-shape-test",
        "OPENAI_MODEL": "fake",
        "OPENAI_USE_RESPONSES": "false",  # scripted_model_server only speaks Chat Completions
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _agent(name: str, *handoffs: str) -> Agent:
    return Agent(name=name, instructions=f"You are {name}.", handoffs=list(handoffs))


async def _receiving_agents_messages(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Drive one handoff (Concierge -> Booking) and return the *second* model call's
    ``messages`` — the request the transferred-to agent (Booking) actually received."""
    received: list[dict[str, Any]] = []
    with scripted_model_server("Friday works.", tool_name=["transfer_to_booking"], received=received) as base_url:
        _wire_openai(monkeypatch, base_url)
        deck = Deck(agents=[_agent("Concierge", "Booking"), _agent("Booking")])
        deck.build()
        async with deck:
            async for _ in deck.stream("Concierge", "book me Friday", session_id="s1"):
                pass
    assert len(received) == 2  # Concierge's turn, then the transferred-to Booking's turn
    return received[1]["messages"]


async def test_default_off_ends_the_transfer_on_an_assistant_turn(
    no_project: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's shape, unguarded: `AGENTDECK_RUNNER_HANDOFF_ENDS_ON_USER_TURN` is unset, so the
    setting stays off and the request ends exactly where the issue's probe found it."""
    messages = await _receiving_agents_messages(monkeypatch)

    assert messages[-1]["role"] == "assistant"
    assert "book me Friday" in messages[-1]["content"]  # the collapsed transcript, inlined as text
    assert not any(message["role"] == "user" for message in messages)  # no user role anywhere


async def test_setting_on_ends_the_transfer_on_a_user_turn(no_project: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_RUNNER_HANDOFF_ENDS_ON_USER_TURN", "true")
    messages = await _receiving_agents_messages(monkeypatch)

    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Please continue."
    # The collapsed summary (today's whole shape) is still there, one turn earlier — this only
    # adds a closing turn, it doesn't replace the SDK's own collapse.
    assert messages[-2]["role"] == "assistant"
    assert "book me Friday" in messages[-2]["content"]


async def test_a_custom_closing_turn_reaches_the_wire(no_project: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default is an English sentence — a non-English deployment overrides it, and that
    override has to be what the model actually receives, not just what a unit test of the mapper
    produces in isolation."""
    monkeypatch.setenv("AGENTDECK_RUNNER_HANDOFF_ENDS_ON_USER_TURN", "true")
    monkeypatch.setenv("AGENTDECK_RUNNER_HANDOFF_CLOSING_TURN", "Bitte fahren Sie fort.")
    messages = await _receiving_agents_messages(monkeypatch)

    assert messages[-1] == {"role": "user", "content": "Bitte fahren Sie fort."}


def test_a_blank_closing_turn_is_refused_at_settings_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty (or whitespace-only) user turn is exactly the shape a provider strict enough to
    need `handoff_ends_on_user_turn` is likely to reject too — refused at boot rather than
    reaching one at request time."""
    monkeypatch.setenv("AGENTDECK_RUNNER_HANDOFF_CLOSING_TURN", "   ")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="AGENTDECK_RUNNER_HANDOFF_CLOSING_TURN cannot be blank"):
        get_settings()
