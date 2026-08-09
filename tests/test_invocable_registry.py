"""The InvocableRegistry: a ``.agentdeck/`` project becomes the specs a Runtime can run.

Discovery is what these tests exercise, so the bundles are real files under a scratch
project dir rather than inline specs — the whole point is that nobody hand-writes the
mapping any more. The agent bundle carries its own scripted model, so a discovered spec
can be played end to end with no key and no network.
"""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest
from agents import Agent
from langgraph.graph.state import CompiledStateGraph, StateGraph

from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind
from agentdeck.errors import ConfigError
from agentdeck.runtime.discovery import ENGINE_FOR_KIND, InvocableRegistry
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from pathlib import Path

    from agentdeck.core.ports import EnginePort

AGENT_PY = '''
from typing import Any

from agents import Agent
from agents.models.interface import Model
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.agents import BaseAgent

_USAGE = ResponseUsage(
    input_tokens=1,
    input_tokens_details=InputTokensDetails(cached_tokens=0),
    output_tokens=1,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    total_tokens=2,
)


class OneLineModel(Model):
    """Answers once, in one message — enough for a run to reach its terminal event."""

    async def stream_response(self, *_args: Any, **_kwargs: Any):
        yield ResponseCompletedEvent(
            response=Response(
                id="resp_discovery",
                created_at=0.0,
                model="fake-discovery",
                object="response",
                output=[
                    ResponseOutputMessage(
                        id="msg_discovery",
                        content=[ResponseOutputText(annotations=[], text="hello", type="output_text")],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ],
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                usage=_USAGE,
            ),
            sequence_number=0,
            type="response.completed",
        )

    async def get_response(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("the fixture bundle only streams")


class {agent_name}(BaseAgent):
    instructions = "Greet the user."

    @classmethod
    def build(cls):
        return Agent(name=cls.__name__, instructions=cls.instructions, model=OneLineModel())
'''

WORKFLOW_PY = """
from typing import TypedDict

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(TypedDict, total=False):
    input: str
    shouted: str


class {workflow_name}(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        graph = StateGraph(cls.state)
        graph.add_node("shout", lambda state: {{"shouted": state["input"].upper()}})
        graph.set_entry_point("shout")
        graph.add_edge("shout", END)
        return graph
"""

SKILL_MD = """---
name: echo-skill
description: Echo input back.
---
Run `scripts/run.py`.
"""


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, agent: str, workflow: str) -> None:
    """Write a scratch ``.agentdeck/`` holding one agent, one workflow and one skill, and cd into it."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY.format(agent_name=agent)))
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(
        textwrap.dedent(WORKFLOW_PY.format(workflow_name=workflow))
    )
    (root / "skills" / "echo-skill" / "scripts").mkdir(parents=True)
    (root / "skills" / "echo-skill" / "SKILL.md").write_text(SKILL_MD)
    (root / "skills" / "echo-skill" / "scripts" / "run.py").touch()
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for module in [name for name in sys.modules if name.startswith("agentdeck_project")]:
        del sys.modules[module]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, agent="Greeter", workflow="Shout")


@pytest.fixture
def engines() -> list[EnginePort]:
    return [OpenAIAgentsEngine(), LangGraphEngine()]


def test_load_compiles_each_bundle_shape_into_a_spec(project: None, engines: list[EnginePort]) -> None:
    specs = InvocableRegistry(engines).load()

    assert sorted(specs) == ["Greeter", "Shout"]
    greeter = specs["Greeter"]
    assert (greeter.name, greeter.kind, greeter.engine) == ("Greeter", InvocableKind.AGENT, "openai-agents")
    assert isinstance(greeter.native, Agent), "an agent bundle's native is the built SDK Agent, not its class"
    shout = specs["Shout"]
    assert (shout.name, shout.kind, shout.engine) == ("Shout", InvocableKind.WORKFLOW, "langgraph")
    assert isinstance(shout.native, StateGraph) and not isinstance(shout.native, CompiledStateGraph), (
        "the langgraph adapter compiles the graph itself, so the spec carries an uncompiled one"
    )


def test_skills_are_not_discovered_as_invocables(project: None, engines: list[EnginePort]) -> None:
    """No engine plays a SKILL.md bundle, so a spec for one could only fail at run time."""
    specs = InvocableRegistry(engines).load()

    assert "echo-skill" not in specs
    assert [spec.kind for spec in specs.values()].count(InvocableKind.SKILL) == 0


def test_kind_to_engine_names_match_the_adapters() -> None:
    """The table spells the engine names out; drift from the adapters would be silent."""
    assert ENGINE_FOR_KIND[InvocableKind.AGENT] == OpenAIAgentsEngine.engine
    assert ENGINE_FOR_KIND[InvocableKind.WORKFLOW] == LangGraphEngine.engine


async def test_discovered_specs_run_to_completion_on_their_engine(project: None, engines: list[EnginePort]) -> None:
    """Both shapes, one Runtime, no inline spec anywhere: discovery output is runnable as-is."""
    runtime = Runtime(engines, MemoryEventStore(), InvocableRegistry(engines).load())

    for name in ("Greeter", "Shout"):
        ctx = RunContext(namespace="acme", run_id=f"r-{name}", session_id=f"s-{name}")
        kinds = [
            event.kind
            async for event in runtime.run(
                name,
                coerce_input("say hi"),
                run_id=(ctx).run_id,
                session_id=(ctx).session_id,
                namespace=(ctx).namespace,
            )
        ]
        assert kinds[0] == "run.started", name
        assert kinds[-1] == "run.completed", name


def test_an_engine_the_runtime_lacks_fails_at_load(project: None) -> None:
    """A project with workflows wired to an openai-agents-only Runtime breaks at startup."""
    registry = InvocableRegistry([OpenAIAgentsEngine()])

    with pytest.raises(ConfigError, match="'Shout' needs engine 'langgraph', which is not registered"):
        registry.load()


def test_one_name_for_two_bundles_fails_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engines: list[EnginePort]
) -> None:
    """v1 keeps agents and workflows in separate namespaces; one flat mapping cannot."""
    _project(tmp_path, monkeypatch, agent="Twin", workflow="Twin")

    with pytest.raises(ConfigError, match="two bundles are both named 'Twin'"):
        InvocableRegistry(engines).load()


def test_a_project_dir_that_is_not_there_fails_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engines: list[EnginePort]
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match=".agentdeck"):
        InvocableRegistry(engines).load()
