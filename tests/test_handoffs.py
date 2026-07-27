"""String-name handoffs (#12): break bidirectional import cycles between agent bundles."""

import sys
import textwrap

import pytest

from agentdeck.agents.registry import AgentRegistry
from agentdeck.errors import NotFoundError

BOOKING_AGENT_PY = """
from agentdeck.agents import BaseAgent

class BookingAgent(BaseAgent):
    handoff_description = "books things"
    handoffs = ["CancelAgent"]
"""

CANCEL_AGENT_PY = """
from agentdeck.agents import BaseAgent

class CancelAgent(BaseAgent):
    handoff_description = "cancels things"
    handoffs = ["BookingAgent"]
"""

GHOST_AGENT_PY = """
from agentdeck.agents import BaseAgent

class GhostAgent(BaseAgent):
    handoffs = ["NoSuchAgent"]
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
    from agentdeck import App

    return App()


@pytest.fixture
def mutual_project(tmp_path, monkeypatch):
    return _mount(tmp_path, monkeypatch, {"booking": BOOKING_AGENT_PY, "cancel": CANCEL_AGENT_PY})


@pytest.fixture
def ghost_project(tmp_path, monkeypatch):
    return _mount(tmp_path, monkeypatch, {"ghost": GHOST_AGENT_PY})


def test_mutual_string_handoffs_both_build(mutual_project):
    registry = AgentRegistry("agentdeck_project")
    booking_cls = registry.get("BookingAgent")
    cancel_cls = registry.get("CancelAgent")

    booking = booking_cls.build()
    cancel = cancel_cls.build()

    assert [h.name for h in booking.handoffs] == ["CancelAgent"]
    assert [h.name for h in cancel.handoffs] == ["BookingAgent"]


def test_app_load_green_with_mutual_handoffs(mutual_project):
    assert sorted(mutual_project.load()["agents"]) == ["BookingAgent", "CancelAgent"]


def test_unknown_handoff_name_raises_not_found_error(ghost_project):
    with pytest.raises(NotFoundError, match="GhostAgent"):
        ghost_project.load()
