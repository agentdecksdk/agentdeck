"""Runtime coverage for handoffs (#248).

``tests/test_handoffs.py`` proves build-time resolution only (mutual naming resolves, an unknown
name raises) and never runs anything. ``tests/test_uc1_handoff.py`` runs exactly one handoff, in
one direction, below the public layer — a hand-built ``Runtime``/``InvocableSpec`` and a
hand-built SDK ``Model``. Mutual ``handoffs=`` is documented (``concepts/agents.mdx``) and
nothing in the suite drives one at runtime through ``Deck`` — this file does.

These tests drive ``Deck`` — the surface users actually have — which means going through the
whole resolved-settings-to-``RunConfig`` path rather than patching a model provider in process
(``agentdeck.testing.patch_model``, what most of ``tests/test_deck.py`` uses): scripting the wire
over ``OPENAI_BASE_URL`` is what proves a *served* run resolves this correctly, the way
``tests/test_ask_agentdeck_server.py`` already does for an ordinary tool call.
``agentdeck.testing.scripted_model_server``'s ``tool_name=`` is widened here (a single name to a
sequence, one call per request) to script the multi-call round trip a handoff cycle needs — see
its docstring.
"""

from __future__ import annotations

from typing import Any

import pytest
from agents.exceptions import MaxTurnsExceeded

from agentdeck.authoring import Agent
from agentdeck.core.context import RunContext
from agentdeck.core.events import Custom, RunCompleted, RunFailed
from agentdeck.deck import Deck
from agentdeck.runtime.settings import get_settings
from agentdeck.testing import scripted_model_server


def _reader_ctx(session_id: str) -> RunContext:
    """A throwaway context for reading a session's log back in a test, same shape as
    ``tests/test_deck.py``'s own helper."""
    return RunContext(run_id="reader", session_id=session_id)


@pytest.fixture
def no_project(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cwd with no ``.agentdeck`` at all, matching every other code-first Deck test in the
    suite — ``Deck(agents=...)`` never reads disk, but there is no reason for this file to be
    the one that assumes a clean cwd instead of arranging it."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _settings_cache() -> Any:
    """``get_settings()`` is process-wide (``lru_cache``); every test here mutates the env vars
    it reads, so the cache is cleared on both sides — before, so this test doesn't inherit a
    previous one's resolution, and after, so the next test file doesn't inherit this one's."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _wire_openai(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    for name, value in {
        "OPENAI_BASE_URL": base_url,
        "OPENAI_API_KEY": "handoff-round-trip-test",
        "OPENAI_MODEL": "fake",
        "OPENAI_USE_RESPONSES": "false",  # scripted_model_server only speaks Chat Completions
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _agent(name: str, *handoffs: str) -> Agent:
    return Agent(name=name, instructions=f"You are {name}.", handoffs=list(handoffs))


def _tool_names(request: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in request.get("tools", [])}


def _handoffs(events: list[Any]) -> list[tuple[str, str]]:
    return [
        (event.payload.data["from"], event.payload.data["to"])
        for event in events
        if isinstance(event.payload, Custom) and event.payload.name == "openai_agents.handoff"
    ]


async def test_a_handoff_cycle_returns_control_and_completes_with_the_final_agents_output(
    no_project: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[dict[str, Any]] = []
    with scripted_model_server(
        "Back with Alpha.", tool_name=["transfer_to_beta", "transfer_to_alpha"], received=received
    ) as base_url:
        _wire_openai(monkeypatch, base_url)
        deck = Deck(agents=[_agent("Alpha", "Beta"), _agent("Beta", "Alpha")])
        deck.build()
        async with deck:
            events = [event async for event in deck.stream("Alpha", "start the cycle", session_id="s1")]
            stored = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    # --- each agent was offered its own transfer tool, and only its own -------------------
    assert _tool_names(received[0]) == {"transfer_to_beta"}  # Alpha's turn: only Beta to hand to
    assert _tool_names(received[1]) == {"transfer_to_alpha"}  # Beta's turn: only Alpha
    assert _tool_names(received[2]) == {"transfer_to_beta"}  # Alpha's turn again: same tool offered

    # --- both handoff records reach the log, in order, with the right from/to -------------
    assert _handoffs(events) == [("Alpha", "Beta"), ("Beta", "Alpha")]
    assert _handoffs(stored) == [("Alpha", "Beta"), ("Beta", "Alpha")]  # the log itself, not just the stream

    # --- the run completes with the final agent's output, not the first agent's -----------
    # (Alpha's first turn is a pure tool call, no message of its own — the only message this
    # run ever produces is the one after the cycle closes, so there is nothing else it could be.)
    completed = next(event.payload for event in events if isinstance(event.payload, RunCompleted))
    assert completed.output[0].text == "Back with Alpha."


async def test_a_three_agent_cycle_also_returns_to_the_first_agent(
    no_project: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A -> B -> C -> A: the two-agent case above is the minimal round trip, but nothing about
    ``link_handoffs`` (or the SDK's own handoff loop) is specific to exactly two agents — this
    proves a longer ring closes the same way rather than leaving that an assumption."""
    received: list[dict[str, Any]] = []
    with scripted_model_server(
        "Back with Alpha, after Beta and Gamma.",
        tool_name=["transfer_to_beta", "transfer_to_gamma", "transfer_to_alpha"],
        received=received,
    ) as base_url:
        _wire_openai(monkeypatch, base_url)
        deck = Deck(
            agents=[
                _agent("Alpha", "Beta"),
                _agent("Beta", "Gamma"),
                _agent("Gamma", "Alpha"),
            ]
        )
        deck.build()
        async with deck:
            events = [event async for event in deck.stream("Alpha", "start the cycle", session_id="s1")]

    assert _tool_names(received[0]) == {"transfer_to_beta"}
    assert _tool_names(received[1]) == {"transfer_to_gamma"}
    assert _tool_names(received[2]) == {"transfer_to_alpha"}
    assert _handoffs(events) == [("Alpha", "Beta"), ("Beta", "Gamma"), ("Gamma", "Alpha")]

    completed = next(event.payload for event in events if isinstance(event.payload, RunCompleted))
    assert completed.output[0].text == "Back with Alpha, after Beta and Gamma."


async def test_a_handoff_cycle_that_never_settles_is_bounded_by_max_turns(
    no_project: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#248's open question: ``RunnerSettings.max_turns`` defaults to 30, presumably stopping two
    agents bouncing a conversation forever, but nothing established that. Set low here so a
    two-agent ping-pong that never elects to stop is a fast, deterministic test — not because the
    bound only holds at this value.
    """
    monkeypatch.setenv("AGENTDECK_RUNNER_MAX_TURNS", "4")
    received: list[dict[str, Any]] = []
    ping_pong = ["transfer_to_beta", "transfer_to_alpha"] * 5  # longer than max_turns, on purpose:
    # if the bound did not hold, this scripts enough turns to prove it kept going past it.
    with scripted_model_server("never reached", tool_name=ping_pong, received=received) as base_url:
        _wire_openai(monkeypatch, base_url)
        deck = Deck(agents=[_agent("Alpha", "Beta"), _agent("Beta", "Alpha")])
        deck.build()
        async with deck:
            events: list[Any] = []
            with pytest.raises(MaxTurnsExceeded):
                async for event in deck.stream("Alpha", "start the cycle", session_id="s1"):
                    events.append(event)

    # It stopped, not hung: exactly `max_turns` model calls were made, not the 10 scripted.
    assert len(received) == 4
    assert isinstance(events[-1].payload, RunFailed)
    assert events[-1].payload.error_code == "engine_error"
    assert "MaxTurnsExceeded" in events[-1].payload.message
