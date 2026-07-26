"""One end-to-end check of the App entry point against a scratch .agentdeck/."""

import asyncio
import sys
import textwrap

import pytest

AGENT_PY = """
from agentdeck.agents import BaseAgent

class Greeter(BaseAgent):
    instructions = "Greet the user."
"""

WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph

class State(BaseModel):
    text: str = ""

class HelloFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"text": s.text.upper()})
        g.set_entry_point("shout")
        g.add_edge("shout", END)
        return g
"""

SKILL_MD = """---
name: echo-skill
description: Echo input back.
---
Run `scripts/run.py`.
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "greeter").mkdir(parents=True)
    (root / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "hello_flow").mkdir()
    (root / "hello_flow" / "workflow.py").write_text(textwrap.dedent(WORKFLOW_PY))
    (root / "skills" / "echo-skill" / "scripts").mkdir(parents=True)
    (root / "skills" / "echo-skill" / "SKILL.md").write_text(SKILL_MD)
    (root / "skills" / "echo-skill" / "scripts" / "run.py").touch()
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    return App()


def test_load_discovers_everything(project):
    assert project.load() == {
        "agents": ["Greeter"],
        "workflows": ["HelloFlow"],
        "skills": ["echo-skill"],
    }


def test_run_workflow(project):
    out = asyncio.run(project.run_workflow("HelloFlow", {"text": "hi"}))
    assert out["text"] == "HI"


def test_sessions_keyed_by_id(project):
    assert project.session_for("a") is project.session_for("a")
    assert project.session_for("a") is not project.session_for("b")


def test_open_close_lifecycle(project):
    """open -> chat-plumbing-level usage (no live model) -> aclose, SQLite fallback."""
    from agentdeck import App

    async def scenario() -> App:
        async with App.open() as app:
            assert app.session_factory is None  # no AGENTDECK_SESSION_REDIS_URL in test env
            assert app.session_for("s1") is app.session_for("s1")
            assert app.load()["agents"] == ["Greeter"]
        return app  # aclose() already ran once via the `async with` exit

    app = asyncio.run(scenario())
    asyncio.run(app.aclose())  # idempotent: closing an already-closed app must not raise
