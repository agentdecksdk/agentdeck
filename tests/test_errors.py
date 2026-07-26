"""One exception hierarchy: registry misses and serve.py's HTTP mapping."""

import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from agentdeck.app import App
from agentdeck.errors import AgentdeckError, NotFoundError, SkillError
from agentdeck.runtime.registry import PluginRegistry
from agentdeck.skills.executor import SkillEnvError, SkillExecutionError

AGENT_PY = """
from agentdeck.agents import BaseAgent

class Greeter(BaseAgent):
    instructions = "Greet the user."
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "greeter").mkdir(parents=True)
    (root / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]


def test_registry_miss_is_agentdeck_error():
    registry = PluginRegistry(package="agentdeck.agents", base_class=object, module_name="agent", label="agent")
    with pytest.raises(AgentdeckError):
        registry.get("does-not-exist")


def test_not_found_error_message_is_plain():
    # serve.py puts str(exc) in the 404 body — no KeyError-style requoting.
    assert str(NotFoundError("no such thing")) == "no such thing"


def test_skill_errors_are_agentdeck_errors():
    assert issubclass(SkillEnvError, SkillError)
    assert issubclass(SkillEnvError, AgentdeckError)
    assert issubclass(SkillExecutionError, SkillError)
    assert issubclass(SkillExecutionError, AgentdeckError)


def test_unknown_agent_chat_returns_404_with_body(project):
    from agentdeck.serve import create_app

    # context manager runs the lifespan; without it every endpoint is 503
    with TestClient(create_app()) as client:
        response = client.post("/agents/unknown/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 404
    assert response.json()["detail"].startswith("No agent named 'unknown'.")


def test_skill_error_returns_500_without_leaking_stderr(project, monkeypatch):
    from agentdeck.serve import create_app

    secret = "Traceback: AWS_SECRET_ACCESS_KEY=hunter2"

    async def boom(self, *_args, **_kwargs):
        raise SkillExecutionError("greeter", 1, secret)

    monkeypatch.setattr(App, "chat", boom)
    with TestClient(create_app()) as client:
        response = client.post("/agents/greeter/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert "hunter2" not in response.text
