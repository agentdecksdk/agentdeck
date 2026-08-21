"""One exception hierarchy: registry misses and serve.py's HTTP mapping."""

import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from agentdeck.errors import AgentdeckError, ConfigError, NotFoundError, SkillError
from agentdeck.runtime.registry import PluginRegistry
from agentdeck.testing import ScriptedModel, patch_model

AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user.")
"""

# Same invocable name as AGENT_PY's, authored under a second bundle  -  the "copied greeter/,
# forgot to rename it" repro from #82.
GREETER_V2_AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user, v2.")
"""

# One bundle, one instance, bound under a second name  -  an alias kept after a rename. Not a
# collision: it is the same object claiming its own name twice, not two different agents.
ALIASED_AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user.")

greeter_agent = greeter
"""

# #174: a bare declaration subclass is exactly what v1 treated as the agent itself  -  the
# natural port of an existing bundle produces this file and used to vanish silently.
GHOST_AGENT_DECLARATION_PY = """
from agentdeck.authoring import AgentDeclaration


class Ghost(AgentDeclaration):
    instructions = "boo"
"""

# A workflow.py that imports cleanly but never calls ``@workflow``  -  the native-catalog
# equivalent of #174's ghost declaration: contributes nothing, and discovery says so.
GHOST_WORKFLOW_PY = """
async def not_a_workflow(text: str) -> str:
    return text
"""

# A shared-code module shaped like a bundle (an `agent.py`, no `Agent(...)` instance) but
# opted out of the scan by its leading underscore  -  the escape hatch #174's check relies on.
SHARED_HELPER_AGENT_PY = """
COMMON_INSTRUCTIONS = "Be helpful."
"""

# #119: `build()`'s job (ModelSettings validation), not `Deck.build()`'s own pre-checks  -  the
# failure only happens once ``InvocableRegistry.load()`` compiles this agent.
BAD_MODEL_SETTINGS_AGENT_PY = """
from agentdeck.authoring import Agent

boom = Agent(name="Boom", instructions="x", model_settings={"temperature": "not-a-number"})
"""

BOOM_WORKFLOW_PY = """
raise ValueError("bad workflow module")
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]


@pytest.fixture
def duplicate_class_name_project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "agents" / "greeter-v2").mkdir(parents=True)
    (root / "agents" / "greeter-v2" / "agent.py").write_text(textwrap.dedent(GREETER_V2_AGENT_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]


@pytest.fixture
def aliased_class_project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(ALIASED_AGENT_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]


def test_registry_miss_is_agentdeck_error():
    registry = PluginRegistry(
        package="agentdeck.agents", base_class=object, module_name="agent", type_dir="agents", label="agent"
    )
    with pytest.raises(AgentdeckError):
        registry.get("does-not-exist")


def test_not_found_error_message_is_plain():
    # serve.py puts str(exc) in the 404 body  -  no KeyError-style requoting.
    assert str(NotFoundError("no such thing")) == "no such thing"


def test_skill_error_is_an_agentdeck_error():
    assert issubclass(SkillError, AgentdeckError)


def test_unknown_agent_chat_returns_404_with_body(project):
    from agentdeck.serve import create_app

    # context manager runs the lifespan; without it every endpoint is 503
    with TestClient(create_app()) as client:
        response = client.post("/agents/unknown/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 404
    assert response.json()["detail"].startswith("No agent named 'unknown'.")


def test_two_same_kind_bundles_sharing_a_class_name_raise_naming_both(duplicate_class_name_project):
    """#82: a copied bundle that forgot to rename its class must not silently shadow the original."""
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError) as excinfo:
        Deck.from_project()
    message = str(excinfo.value)
    # Quoted, not bare substrings: "agents/greeter" is itself a substring of
    # "agents/greeter-v2", so a bare-substring check passes even if the message only
    # ever named the second bundle  -  pin the exact quoted forms the message emits.
    assert "'agents/greeter'" in message
    assert "'agents/greeter-v2'" in message
    assert message.count("Greeter") >= 1


def test_one_bundle_aliasing_its_own_class_is_not_a_collision(aliased_class_project):
    """A bundle binding one class under two names (an alias kept after a rename) must still load.

    ``vars(module)`` yields one entry per *binding*, not per class  -  ``GreeterAgent = Greeter``
    must not trip the same-name guard against itself.
    """
    from agentdeck.deck import Deck

    deck = Deck.from_project()
    assert list(deck.agents) == ["Greeter"]


def test_bundle_import_failure_is_wrapped_with_its_path(tmp_path, monkeypatch):
    """A bundle that raises at import (SyntaxError, missing dep) used to surface a raw traceback."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "broken").mkdir(parents=True)
    (root / "agents" / "broken" / "agent.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError, match="agents/broken/agent.py") as excinfo:
        Deck.from_project()
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_old_layout_raises_clear_config_error(tmp_path, monkeypatch):
    """A pre-0.3 project (bundles straight under the project root) fails loudly, not silently."""
    root = tmp_path / ".agentdeck"
    (root / "greeter").mkdir(parents=True)
    (root / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError, match="agents/<bundle>/agent.py"):
        Deck.from_project()


def _drop_project_mount(monkeypatch):
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]


def test_agent_declaration_never_instantiated_raises_naming_the_bundle(tmp_path, monkeypatch):
    """#174: v1 scanned for a subclass, so `class Ghost(AgentDeclaration)` alone *was* the agent.
    v3 scans for instances  -  the natural port of an existing bundle imports cleanly and used to
    contribute nothing, with `from_project().agents` silently `{}`.
    """
    root = tmp_path / ".agentdeck"
    (root / "agents" / "ghost").mkdir(parents=True)
    (root / "agents" / "ghost" / "agent.py").write_text(textwrap.dedent(GHOST_AGENT_DECLARATION_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError, match="agents/ghost/agent.py") as excinfo:
        Deck.from_project()
    message = str(excinfo.value)
    assert "defines no agent" in message
    assert "Agent(...)" in message


def test_a_workflow_module_defining_no_workflow_raises_naming_the_bundle(tmp_path, monkeypatch):
    """The same ghost check as #174's, on the native catalog: a module with no ``@workflow``
    contributes nothing, and discovery says so rather than silently discarding it."""
    root = tmp_path / ".agentdeck"
    (root / "workflows" / "ghost_flow").mkdir(parents=True)
    (root / "workflows" / "ghost_flow" / "workflow.py").write_text(textwrap.dedent(GHOST_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError, match="workflows/ghost_flow/workflow.py") as excinfo:
        Deck.from_project()
    message = str(excinfo.value)
    assert "defines no workflow" in message
    assert "NativeDefinition(...)" in message


def test_ghost_check_suggests_a_valid_identifier_for_a_hyphenated_bundle(tmp_path, monkeypatch):
    """The suggested fix must itself be legal Python  -  a bundle dir name is not required to be."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "ghost-agent").mkdir(parents=True)
    (root / "agents" / "ghost-agent" / "agent.py").write_text(textwrap.dedent(GHOST_AGENT_DECLARATION_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError) as excinfo:
        Deck.from_project()
    assert "ghost-agent = Agent(...)" not in str(excinfo.value)
    assert "ghost_agent = Agent(...)" in str(excinfo.value)


def test_a_leading_underscore_bundle_dir_is_not_scanned_even_with_no_instance(tmp_path, monkeypatch):
    """The escape hatch #174's check relies on: shared code that happens to live under
    ``agents/``/``workflows/`` opts out of bundle scanning the same way it already does for
    the collision/import checks  -  a leading ``_``/``.`` on the directory name.
    """
    root = tmp_path / ".agentdeck"
    (root / "agents" / "_shared").mkdir(parents=True)
    (root / "agents" / "_shared" / "agent.py").write_text(textwrap.dedent(SHARED_HELPER_AGENT_PY))
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    deck = Deck.from_project()
    assert list(deck.agents) == ["Greeter"]


def test_discovered_agent_build_failure_is_wrapped_with_its_bundle_path(tmp_path, monkeypatch):
    """#119: `build()`/`build_graph()` failures used to surface a bare exception with no
    indication of which bundle caused it  -  the same problem #82 fixed for import, one step
    later in the same pipeline. `ModelSettings` validation only runs once `Deck.build()`
    compiles the agent, so this is not caught by any of `Deck.build()`'s own pre-checks.
    """
    root = tmp_path / ".agentdeck"
    (root / "agents" / "boom").mkdir(parents=True)
    (root / "agents" / "boom" / "agent.py").write_text(textwrap.dedent(BAD_MODEL_SETTINGS_AGENT_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    deck = Deck.from_project()
    with pytest.raises(ConfigError, match="agents/boom/agent.py") as excinfo:
        deck.build()
    assert "failed to build" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None
    import pydantic

    assert isinstance(excinfo.value.__cause__, pydantic.ValidationError)


def test_a_broken_workflow_modules_import_failure_is_wrapped_with_its_bundle_path(tmp_path, monkeypatch):
    """The workflow-side counterpart to ``test_bundle_import_failure_is_wrapped_with_its_path``:
    a native workflow's whole contract is checked by ``@workflow`` at decoration time, which
    runs as the module imports, so a broken bundle here fails the same way an agent's does."""
    root = tmp_path / ".agentdeck"
    (root / "workflows" / "boom").mkdir(parents=True)
    (root / "workflows" / "boom" / "workflow.py").write_text(textwrap.dedent(BOOM_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    _drop_project_mount(monkeypatch)
    from agentdeck.deck import Deck

    with pytest.raises(ConfigError, match="workflows/boom/workflow.py") as excinfo:
        Deck.from_project()
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert str(excinfo.value.__cause__) == "bad workflow module"


def test_code_first_agent_build_failure_is_not_wrapped_with_a_bundle_path():
    """A code-first agent has no bundle to name  -  #119 only covers discovery, so the raw
    exception must reach the caller unchanged, exactly as it did before this fix.
    """
    from agentdeck.authoring import Agent
    from agentdeck.deck import Deck

    boom = Agent(name="Boom", instructions="x", model_settings={"temperature": "not-a-number"})
    deck = Deck(agents=[boom])

    with pytest.raises(Exception) as excinfo:  # noqa: PT011  -  asserting it's specifically *not* a ConfigError
        deck.build()
    assert not isinstance(excinfo.value, ConfigError)


def test_skill_error_returns_500_without_leaking_stderr(project):
    from agentdeck.serve import create_app

    secret = "Traceback: AWS_SECRET_ACCESS_KEY=hunter2"
    # The turn fails at the SDK boundary, so the error travels the whole real path  -  engine,
    # Runtime, surface  -  the way a failing tool or skill inside a turn does.
    model = ScriptedModel(raises=SkillError(secret))
    with patch_model(model), TestClient(create_app()) as client:
        response = client.post("/agents/Greeter/chat", json={"session_id": "s", "message": "hi"})
    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    assert "hunter2" not in response.text
