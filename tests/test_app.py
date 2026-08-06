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
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "workflows" / "hello_flow").mkdir(parents=True)
    (root / "workflows" / "hello_flow" / "workflow.py").write_text(textwrap.dedent(WORKFLOW_PY))
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


@pytest.fixture(autouse=True)
def _reset_mcp_lifecycle():
    from agentdeck.agents.mcp.lifecycle import MCPLifecycle

    yield
    MCPLifecycle.reset()


class FakeSessionFactory:
    """Stand-in for the Redis-backed SessionFactory; counts aclose() calls."""

    def __init__(self):
        self.closed = 0
        self.sessions = {}

    def session_for(self, session_id):
        from agents import SQLiteSession

        return self.sessions.setdefault(session_id, SQLiteSession(session_id))

    async def aclose(self):
        self.closed += 1


def test_open_close_lifecycle(project):
    """open -> chat-plumbing-level usage (no live model) -> aclose, SQLite fallback."""
    from agentdeck import App

    async def scenario() -> App:
        async with App.open() as app:
            assert app.session_factory is None  # no AGENTDECK_SESSION_REDIS_URL in test env
            assert app.session_for("s1") is app.session_for("s1")
            assert app.inventory["agents"] == ["Greeter"]  # load() ran and stashed the inventory
        return app  # aclose() already ran once via the `async with` exit

    app = asyncio.run(scenario())
    asyncio.run(app.aclose())  # idempotent: closing an already-closed app must not raise


def test_only_the_app_that_started_mcp_shuts_it_down(project, monkeypatch):
    """The MCP registry is process-wide: a bare App must not tear down someone else's servers."""
    from agentdeck import App
    from agentdeck.agents.mcp.lifecycle import MCPLifecycle

    calls = []

    async def spy():
        calls.append(1)

    monkeypatch.setattr(MCPLifecycle, "shutdown", staticmethod(spy))

    asyncio.run(App().aclose())
    assert calls == []

    async def scenario():
        async with App.open():
            pass

    asyncio.run(scenario())
    assert calls == [1]


def test_injected_session_factory_is_used_and_closed_once(project, monkeypatch):
    """The DI seam bypasses `from_settings` and `aclose()` closes the injection exactly once."""
    from agentdeck import App
    from agentdeck.runtime.sessions import SessionFactory

    def boom(_settings):
        raise AssertionError("from_settings must not be called when a factory is injected")

    monkeypatch.setattr(SessionFactory, "from_settings", staticmethod(boom))
    fake = FakeSessionFactory()

    async def scenario() -> App:
        async with App.open(session_factory=fake) as app:
            assert app.session_factory is fake
            assert app.session_for("s1") is fake.sessions["s1"]
        return app

    app = asyncio.run(scenario())
    assert fake.closed == 1
    asyncio.run(app.aclose())
    assert fake.closed == 1


def test_injected_session_factory_closed_when_load_fails(tmp_path, monkeypatch):
    """A failure inside open() (broken bundle) must still close the injected factory."""
    from agentdeck.errors import ConfigError

    root = tmp_path / ".agentdeck"
    (root / "agents" / "broken").mkdir(parents=True)
    (root / "agents" / "broken" / "agent.py").write_text("raise RuntimeError('broken bundle')\n")
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    fake = FakeSessionFactory()

    async def scenario():
        async with App.open(session_factory=fake):
            pass

    # the raw RuntimeError is now wrapped in a ConfigError naming the offending bundle path
    with pytest.raises(ConfigError, match="agents/broken/agent.py"):
        asyncio.run(scenario())
    assert fake.closed == 1


def test_old_layout_raises_clear_config_error(tmp_path, monkeypatch):
    """A pre-0.3 project (bundles straight under the project root) fails loudly, not silently."""
    from agentdeck.errors import ConfigError

    root = tmp_path / ".agentdeck"
    (root / "greeter").mkdir(parents=True)
    (root / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    with pytest.raises(ConfigError, match="agents/<bundle>/agent.py"):
        App().load()
