"""One exception hierarchy: registry misses and serve.py's HTTP mapping."""

import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from agentdeck.errors import AgentdeckError, NotFoundError, SkillError
from agentdeck.runtime.registry import PluginRegistry
from agentdeck.skills.executor import SkillEnvError
from agentdeck.workflows.nodes import SkillExecutionError

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


def test_not_found_error_is_also_key_error():
    # Compat: registries raised bare KeyError before this hierarchy existed.
    assert issubclass(NotFoundError, KeyError)
    with pytest.raises(KeyError):
        raise NotFoundError("no such thing")


def test_skill_errors_are_agentdeck_and_runtime_errors():
    # Compat: both predate SkillError and are caught via `except RuntimeError` elsewhere.
    assert issubclass(SkillEnvError, SkillError)
    assert issubclass(SkillEnvError, AgentdeckError)
    assert issubclass(SkillEnvError, RuntimeError)
    assert issubclass(SkillExecutionError, SkillError)
    assert issubclass(SkillExecutionError, AgentdeckError)
    assert issubclass(SkillExecutionError, RuntimeError)


def test_unknown_agent_chat_returns_404_with_body(project):
    from agentdeck.serve import create_app

    client = TestClient(create_app())
    response = client.post("/agents/unknown/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 404
    assert "unknown" in response.json()["detail"]
