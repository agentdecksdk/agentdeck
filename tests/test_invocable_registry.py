"""The InvocableRegistry: a ``.agentdeck/`` project becomes the specs a Runtime can run.

Discovery is what these tests exercise, so the bundles are real files under a scratch
project dir rather than inline specs  -  the whole point is that nobody hand-writes the
mapping any more. The agent bundle carries its own scripted model, so a discovered spec
can be played end to end with no key and no network.
"""

from __future__ import annotations

import sys
import textwrap
from typing import TYPE_CHECKING

import pytest
from agents import Agent

from agentdeck.adapters.executors.native import NativeExecutor
from agentdeck.adapters.executors.openai_agents import OpenAIAgentsExecutor
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.authoring.native import NativeDefinition
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind
from agentdeck.errors import ConfigError
from agentdeck.runtime.discovery import EXECUTOR_FOR_KIND, InvocableRegistry
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from pathlib import Path

    from agentdeck.core.ports import Executor

AGENT_PY = '''
from typing import Any

from agents.models.interface import Model
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.authoring import Agent

_USAGE = ResponseUsage(
    input_tokens=1,
    input_tokens_details=InputTokensDetails(cached_tokens=0),
    output_tokens=1,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    total_tokens=2,
)


class OneLineModel(Model):
    """Answers once, in one message  -  enough for a run to reach its terminal event."""

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


{agent_var} = Agent(name="{agent_name}", instructions="Greet the user.", model=OneLineModel())
'''

WORKFLOW_PY = """
from agentdeck import WorkflowCtx, workflow


@workflow(name="{workflow_name}")
async def {workflow_var}(ctx: WorkflowCtx, text: str) -> str:
    return text.upper()
"""

SKILL_MD = """---
name: echo-skill
description: Echo input back.
---
Run `scripts/run.py`.
"""

# #488: a workflow bundle also exporting a `@tool`  -  must not surface as a second spec.
TOOL_AND_WORKFLOW_PY = """
from agentdeck import ToolCtx, WorkflowCtx, tool, workflow


@tool
async def helper(ctx: ToolCtx, text: str) -> str:
    return text


@workflow(name="Shout")
async def shout(ctx: WorkflowCtx, text: str) -> str:
    return text.upper()
"""


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, agent: str, workflow: str) -> None:
    """Write a scratch ``.agentdeck/`` holding one agent, one workflow and one skill, and cd into it."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(
        textwrap.dedent(AGENT_PY.format(agent_name=agent, agent_var=agent.lower()))
    )
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(
        textwrap.dedent(WORKFLOW_PY.format(workflow_name=workflow, workflow_var=workflow.lower()))
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
def executors() -> list[Executor]:
    return [OpenAIAgentsExecutor(), NativeExecutor()]


def test_load_compiles_each_bundle_shape_into_a_spec(project: None, executors: list[Executor]) -> None:
    specs = InvocableRegistry(executors).load()

    assert sorted(specs) == ["Greeter", "Shout"]
    greeter = specs["Greeter"]
    assert (greeter.name, greeter.kind, greeter.executor) == ("Greeter", InvocableKind.AGENT, "openai-agents")
    assert isinstance(greeter.native, Agent), "an agent bundle's native is the built SDK Agent, not its class"
    shout = specs["Shout"]
    assert (shout.name, shout.kind, shout.executor) == ("Shout", InvocableKind.WORKFLOW, "native")
    assert isinstance(shout.native, NativeDefinition), "a native workflow's spec carries its own decorated definition"


def test_a_bundled_tool_still_compiles_into_the_runtime_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executors: list[Executor]
) -> None:
    """#488 is a `Deck.workflows`/public-name concern, not a compile-time one: ``specs`` is the
    Runtime's own catalog, kind-blind by design, so a bundled ``@tool`` still needs a spec here
    for ``ctx.invoke`` to find  -  ``Deck.workflows`` and ``Deck.run`` are what hide it, and
    ``load()`` called with no explicit ``workflows=`` is the path ``Deck.build()`` never takes
    (it always passes its own already-discovered list) but ``InvocableRegistry(...).load()`` is
    public and callable directly.
    """
    root = tmp_path / ".agentdeck"
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(textwrap.dedent(TOOL_AND_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for module in [name for name in sys.modules if name.startswith("agentdeck_project")]:
        del sys.modules[module]

    specs = InvocableRegistry(executors).load()

    assert sorted(specs) == ["Shout", "helper"]
    assert specs["helper"].kind is InvocableKind.TOOL


def test_skills_are_not_discovered_as_invocables(project: None, executors: list[Executor]) -> None:
    """No executor plays a SKILL.md bundle, so a spec for one could only fail at run time."""
    specs = InvocableRegistry(executors).load()

    assert "echo-skill" not in specs
    assert [spec.kind for spec in specs.values()].count(InvocableKind.SKILL) == 0


def test_kind_to_engine_names_match_the_adapters() -> None:
    """The table spells the engine names out; drift from the adapters would be silent."""
    assert EXECUTOR_FOR_KIND[InvocableKind.AGENT] == OpenAIAgentsExecutor.name


async def test_discovered_specs_run_to_completion_on_their_executor(project: None, executors: list[Executor]) -> None:
    """Both shapes, one Runtime, no inline spec anywhere: discovery output is runnable as-is."""
    runtime = Runtime(executors, MemoryEventStore(), InvocableRegistry(executors).load())

    for name in ("Greeter", "Shout"):
        ctx = RunContext(namespace="acme", run_id=f"r-{name}", session_id=f"s-{name}")
        kinds = [
            event.kind
            async for event in runtime.run(
                name,
                coerce_input("say hi"),
                session_id=(ctx).session_id,
                namespace=(ctx).namespace,
            )
        ]
        assert kinds[0] == "run.started", name
        assert kinds[-1] == "run.completed", name


def test_one_name_for_two_bundles_fails_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executors: list[Executor]
) -> None:
    """v1 keeps agents and workflows in separate namespaces; one flat mapping cannot."""
    _project(tmp_path, monkeypatch, agent="Twin", workflow="Twin")

    with pytest.raises(ConfigError, match="an agent and a workflow are both named 'Twin'"):
        InvocableRegistry(executors).load()


def test_a_project_dir_that_is_not_there_fails_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executors: list[Executor]
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FileNotFoundError, match=".agentdeck"):
        InvocableRegistry(executors).load()


def test_a_discovered_workflow_modules_import_failure_is_wrapped_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executors: list[Executor]
) -> None:
    """#119, on the bare ``load()`` path with no ``Deck`` involved (``build_runtime``'s own
    default caller): the bundle association discovery built internally must still reach the
    wrap, not just the one ``Deck.from_project`` threads through explicitly.
    """
    root = tmp_path / ".agentdeck"
    (root / "workflows" / "boom").mkdir(parents=True)
    (root / "workflows" / "boom" / "workflow.py").write_text('raise ValueError("bad workflow module")\n')
    monkeypatch.chdir(tmp_path)
    for module in [name for name in sys.modules if name.startswith("agentdeck_project")]:
        del sys.modules[module]

    with pytest.raises(ConfigError, match="workflows/boom/workflow.py") as excinfo:
        InvocableRegistry(executors).load()
    assert isinstance(excinfo.value.__cause__, ValueError)
