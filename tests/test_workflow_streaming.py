"""``run_workflow_stream`` (issue #9): ``node_update``/``custom`` events per LangGraph's
``astream(stream_mode=["updates", "custom"])``, then one terminal ``done`` event carrying the
final state — plus ``AgentNode`` forwarding its nested agent's deltas into the custom stream.
"""

import json
import sys
import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

AGENT_PY = """
from agentdeck.agents import BaseAgent

class Greeter(BaseAgent):
    instructions = "Greet the user."
"""

TWO_STEP_WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph

class State(BaseModel):
    text: str = ""
    count: int = 0

class TwoStepFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"text": s.text.upper()})
        g.add_node("count_up", lambda s: {"count": s.count + 1})
        g.set_entry_point("shout")
        g.add_edge("shout", "count_up")
        g.add_edge("count_up", END)
        return g
"""

AGENT_FLOW_WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, AgentNode, BaseWorkflow, StateGraph
from agentdeck_project.greeter.agent import Greeter

class State(BaseModel):
    input: str = ""
    output: str = ""

class ChatFlow(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("greet", AgentNode(Greeter, input_key="input", output_key="output"))
        g.set_entry_point("greet")
        g.add_edge("greet", END)
        return g
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "greeter").mkdir(parents=True)
    (root / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "two_step").mkdir()
    (root / "two_step" / "workflow.py").write_text(textwrap.dedent(TWO_STEP_WORKFLOW_PY))
    (root / "agent_flow").mkdir()
    (root / "agent_flow" / "workflow.py").write_text(textwrap.dedent(AGENT_FLOW_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    return App()


def _delta_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="raw_response_event",
        data=SimpleNamespace(type="response.output_text.delta", delta=text),
    )


@dataclass
class FakeRunResultStreaming:
    """Duck-types ``agents.result.RunResultStreaming`` for the surface run_streamed uses."""

    events: list
    final_output: str
    cancelled: int = 0
    context_wrapper: object = field(
        default_factory=lambda: SimpleNamespace(
            usage=SimpleNamespace(requests=1, input_tokens=1, output_tokens=1, total_tokens=2)
        )
    )

    async def stream_events(self):
        for event in self.events:
            yield event

    def cancel(self, mode="immediate"):
        self.cancelled += 1


async def _collect(agen):
    return [event async for event in agen]


async def test_run_workflow_stream_yields_one_node_update_per_node_then_done(project):
    events = await _collect(project.run_workflow_stream("TwoStepFlow", {"text": "hi"}))

    assert events == [
        {"type": "node_update", "node": "shout", "delta": {"text": "HI"}},
        {"type": "node_update", "node": "count_up", "delta": {"count": 1}},
        {"type": "done", "state": {"text": "HI", "count": 1}},
    ]


async def test_run_workflow_stream_agent_node_forwards_deltas_via_custom_stream(project, monkeypatch):
    events = [_delta_event("Hel"), _delta_event("lo"), _delta_event("!")]
    fake_result = FakeRunResultStreaming(events=events, final_output="Hello!")

    def boom(agent, message, **kwargs):
        raise AssertionError("run_workflow_stream() must not touch Runner.run")

    monkeypatch.setattr("agentdeck.agents.runners.headless.Runner.run", boom)
    monkeypatch.setattr(
        "agentdeck.agents.runners.headless.Runner.run_streamed",
        lambda agent, message, **kwargs: fake_result,
    )

    stream_events = await _collect(project.run_workflow_stream("ChatFlow", {"input": "hi"}))

    custom_events = [e for e in stream_events if e["type"] == "custom"]
    assert custom_events == [
        {"type": "custom", "data": "Hel"},
        {"type": "custom", "data": "lo"},
        {"type": "custom", "data": "!"},
    ]
    assert stream_events[-1] == {"type": "done", "state": {"input": "hi", "output": "Hello!"}}
    # node_update still fires once the agent node resolves, carrying the final output only.
    node_updates = [e for e in stream_events if e["type"] == "node_update"]
    assert node_updates == [{"type": "node_update", "node": "greet", "delta": {"output": "Hello!"}}]


async def test_run_workflow_unchanged_when_not_streamed(project, monkeypatch):
    """``run_workflow`` still drives the graph through a single ``ainvoke`` call."""
    calls = []
    real_ainvoke = project.workflows.get("TwoStepFlow").build().ainvoke

    async def spy_ainvoke(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_ainvoke(*args, **kwargs)

    monkeypatch.setattr(project.workflows.get("TwoStepFlow").build(), "ainvoke", spy_ainvoke)

    out = await project.run_workflow("TwoStepFlow", {"text": "hi"})

    assert out == {"text": "HI", "count": 1}
    assert len(calls) == 1  # a single ainvoke, exactly as before this feature existed


async def test_agent_node_uses_plain_run_when_workflow_is_not_streamed(project, monkeypatch):
    """The substantive guarantee: an AgentNode inside a plain run_workflow() call must go
    through Runner.run, never Runner.run_streamed — a detached streaming task has different
    cancellation/exception-timing semantics, so get_stream_writer() no-op-ing alone isn't enough.
    """
    run_calls = []
    run_streamed_calls = []

    async def fake_run(agent, message, **kwargs):
        run_calls.append((agent, message))
        return SimpleNamespace(final_output="Hello!", context_wrapper=SimpleNamespace(usage=None))

    def fake_run_streamed(agent, message, **kwargs):
        run_streamed_calls.append((agent, message))
        raise AssertionError("run_workflow() must not touch Runner.run_streamed")

    monkeypatch.setattr("agentdeck.agents.runners.headless.Runner.run", fake_run)
    monkeypatch.setattr("agentdeck.agents.runners.headless.Runner.run_streamed", fake_run_streamed)

    out = await project.run_workflow("ChatFlow", {"input": "hi"})

    assert out == {"input": "hi", "output": "Hello!"}
    assert len(run_calls) == 1
    assert run_streamed_calls == []


def _sse_frames(text: str) -> list[tuple[str, dict]]:
    frames = []
    for block in text.strip().split("\n\n"):
        name = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        frames.append((name, json.loads(data)))
    return frames


@pytest.fixture
def client(project):
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    with TestClient(create_app()) as c:
        yield c


def test_workflow_stream_endpoint_emits_updates_then_done(client, monkeypatch):
    async def fake_run_workflow_stream(self, name, state, **_):
        yield {"type": "node_update", "node": "shout", "delta": {"text": "HI"}}
        yield {"type": "custom", "data": "chunk"}
        yield {"type": "done", "state": {"text": "HI", "count": 1}}

    monkeypatch.setattr("agentdeck.app.App.run_workflow_stream", fake_run_workflow_stream)

    response = client.post("/workflows/TwoStepFlow?stream=true", json={"text": "hi"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert _sse_frames(response.text) == [
        ("message", {"type": "node_update", "node": "shout", "delta": {"text": "HI"}}),
        ("message", {"type": "custom", "data": "chunk"}),
        ("done", {"text": "HI", "count": 1}),
    ]


def test_workflow_stream_endpoint_reports_mid_stream_failure(client, monkeypatch):
    async def exploding_run_workflow_stream(self, name, state, **_):
        yield {"type": "node_update", "node": "shout", "delta": {"text": "HI"}}
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("agentdeck.app.App.run_workflow_stream", exploding_run_workflow_stream)

    response = client.post("/workflows/TwoStepFlow?stream=true", json={"text": "hi"})

    frames = _sse_frames(response.text)
    assert frames[0] == ("message", {"type": "node_update", "node": "shout", "delta": {"text": "HI"}})
    assert frames[-1] == ("error", {"error": "RuntimeError"})
    assert "secret internal detail" not in response.text


def test_run_workflow_endpoint_unchanged_when_not_streamed(client):
    response = client.post("/workflows/TwoStepFlow", json={"text": "hi"})

    assert response.status_code == 200
    assert response.json() == {"text": "HI", "count": 1}
