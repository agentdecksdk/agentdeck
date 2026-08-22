"""String-name handoffs (#12): break bidirectional import cycles between agent bundles.

Resolution moved from ``BaseAgent.build()``'s own disk lookup (v1) to
``InvocableRegistry.load()``'s two-pass compile (``authoring.compile.link_handoffs``,
#164): every agent in a project is discovered before any handoff is resolved, so a
cycle between two bundle files still needs no import from one to the other.
"""

import sys
import textwrap

import pytest

from agentdeck.errors import NotFoundError

BOOKING_AGENT_PY = """
from agentdeck.authoring import Agent

booking_agent = Agent(name="BookingAgent", handoff_description="books things", handoffs=["CancelAgent"])
"""

CANCEL_AGENT_PY = """
from agentdeck.authoring import Agent

cancel_agent = Agent(name="CancelAgent", handoff_description="cancels things", handoffs=["BookingAgent"])
"""

GHOST_AGENT_PY = """
from agentdeck.authoring import Agent

ghost_agent = Agent(name="GhostAgent", handoffs=["NoSuchAgent"])
"""


def _mount(tmp_path, monkeypatch, bundles: dict[str, str]):
    root = tmp_path / ".agentdeck" / "agents"
    for bundle, source in bundles.items():
        (root / bundle).mkdir(parents=True)
        (root / bundle / "agent.py").write_text(textwrap.dedent(source))
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck.deck import Deck

    return Deck.from_project()


@pytest.fixture
def mutual_project(tmp_path, monkeypatch):
    return _mount(tmp_path, monkeypatch, {"booking": BOOKING_AGENT_PY, "cancel": CANCEL_AGENT_PY})


@pytest.fixture
def ghost_project(tmp_path, monkeypatch):
    return _mount(tmp_path, monkeypatch, {"ghost": GHOST_AGENT_PY})


def test_mutual_string_handoffs_both_build(mutual_project):
    from agentdeck.adapters.executors.openai_agents import OpenAIAgentsExecutor
    from agentdeck.runtime.discovery import InvocableRegistry

    specs = InvocableRegistry([OpenAIAgentsExecutor()]).load()
    booking = specs["BookingAgent"].native
    cancel = specs["CancelAgent"].native

    assert [h.name for h in booking.handoffs] == ["CancelAgent"]
    assert [h.name for h in cancel.handoffs] == ["BookingAgent"]
    assert booking.handoffs[0] is cancel  # cycle resolves to the same compiled instance, not a placeholder
    assert cancel.handoffs[0] is booking


def test_deck_builds_green_with_mutual_handoffs(mutual_project):
    mutual_project.build()
    assert sorted(mutual_project.agents) == ["BookingAgent", "CancelAgent"]


def test_unknown_handoff_name_raises_not_found_error(ghost_project):
    with pytest.raises(NotFoundError, match="GhostAgent"):
        ghost_project.build()
