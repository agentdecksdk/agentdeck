"""``run_workflow_stream`` (issue #9): ``node_update``/``custom`` events per LangGraph's
``astream(stream_mode=["updates", "custom"])``, then one terminal ``done`` event carrying the
final state — plus ``AgentNode`` forwarding its nested agent's deltas into the custom stream.

The two endpoint tests at the bottom drive the whole real path instead of stubbing
``Deck.run_workflow_stream``, which the streamed endpoint no longer calls: it renders
v1's frames from the canonical events of a Runtime run.
"""

import json
import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user.")
"""

WRITER_WORKFLOW_PY = """
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.errors import SkillError
from agentdeck.authoring import Workflow

SECRET = "stderr: AGENTDECK_TOKEN=sk-do-not-leak"

class State(BaseModel):
    text: str = ""
    count: int = 0

def _write(state):
    get_stream_writer()("chunk")
    return {"count": 1}

def _build_writer_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"text": s.text.upper()})
    g.add_node("write", _write)
    g.set_entry_point("shout")
    g.add_edge("shout", "write")
    g.add_edge("write", END)
    return g

writer_flow = Workflow(name="WriterFlow", state=State, graph=_build_writer_graph)

def _explode(state):
    raise SkillError(SECRET)

def _build_halfway_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"text": s.text.upper()})
    g.add_node("explode", _explode)
    g.set_entry_point("shout")
    g.add_edge("shout", "explode")
    g.add_edge("explode", END)
    return g

halfway_flow = Workflow(name="HalfwayFlow", state=State, graph=_build_halfway_graph)
"""

TWO_STEP_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.authoring import Workflow

class State(BaseModel):
    text: str = ""
    count: int = 0

def _build_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"text": s.text.upper()})
    g.add_node("count_up", lambda s: {"count": s.count + 1})
    g.set_entry_point("shout")
    g.add_edge("shout", "count_up")
    g.add_edge("count_up", END)
    return g

two_step_flow = Workflow(name="TwoStepFlow", state=State, graph=_build_graph)
"""

AGENT_FLOW_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.authoring import AgentNode, Workflow
from agentdeck_project.agents.greeter.agent import greeter

class State(BaseModel):
    input: str = ""
    output: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("greet", AgentNode(greeter, input_key="input", output_key="output"))
    g.set_entry_point("greet")
    g.add_edge("greet", END)
    return g

chat_flow = Workflow(name="ChatFlow", state=State, graph=_build_graph)
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The project directory only — a deck of this suite's own and the server's are two
    different decks, and one Deck holds the process at a time."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "workflows" / "two_step").mkdir(parents=True)
    (root / "workflows" / "two_step" / "workflow.py").write_text(textwrap.dedent(TWO_STEP_WORKFLOW_PY))
    (root / "workflows" / "agent_flow").mkdir(parents=True)
    (root / "workflows" / "agent_flow" / "workflow.py").write_text(textwrap.dedent(AGENT_FLOW_WORKFLOW_PY))
    (root / "workflows" / "writer").mkdir(parents=True)
    (root / "workflows" / "writer" / "workflow.py").write_text(textwrap.dedent(WRITER_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
async def deck(project):  # noqa: ARG001 — the project dir is what `from_project()` discovers
    from agentdeck.deck import Deck

    deck = Deck.from_project()
    yield deck
    await deck.aclose()


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


async def test_stream_yields_one_node_updated_event_per_node_then_run_completed(deck):
    from agentdeck.core.events import NodeUpdated

    async with deck:
        events = await _collect(deck.stream("TwoStepFlow", {"text": "hi"}))

    assert [event.kind for event in events] == ["run.started", "node.updated", "node.updated", "run.completed"]
    updates = [event.payload for event in events if isinstance(event.payload, NodeUpdated)]
    assert [(u.node, u.state_patch) for u in updates] == [("shout", {"text": "HI"}), ("count_up", {"count": 1})]


async def test_stream_agent_node_forwards_deltas_via_custom_events(deck, monkeypatch):
    from agentdeck.adapters.engines.langgraph.engine import STREAM_WRITE, STREAM_WRITE_KEY
    from agentdeck.core.events import Custom, NodeUpdated

    events = [_delta_event("Hel"), _delta_event("lo"), _delta_event("!")]
    fake_result = FakeRunResultStreaming(events=events, final_output="Hello!")

    def boom(agent, message, **kwargs):
        raise AssertionError("stream() must not touch Runner.run")

    monkeypatch.setattr("agentdeck.authoring.runners.agent.Runner.run", boom)
    monkeypatch.setattr(
        "agentdeck.authoring.runners.agent.Runner.run_streamed",
        lambda agent, message, **kwargs: fake_result,
    )

    async with deck:
        stream_events = await _collect(deck.stream("ChatFlow", {"input": "hi"}))

    customs = [
        event.payload.data[STREAM_WRITE_KEY]
        for event in stream_events
        if isinstance(event.payload, Custom) and event.payload.name == STREAM_WRITE
    ]
    assert customs == ["Hel", "lo", "!"]
    # node.updated still fires once the agent node resolves, carrying the final output only.
    updates = [event.payload for event in stream_events if isinstance(event.payload, NodeUpdated)]
    assert [(u.node, u.state_patch) for u in updates] == [("greet", {"output": "Hello!"})]


async def test_run_now_drives_the_graph_through_the_runtimes_stream(deck, monkeypatch):
    """``run`` used to call the compiled graph's ``ainvoke`` once; now that it plays
    on the Runtime it drives the same ``astream`` every other invocable does,
    consumed to its end rather than left for a caller to iterate — ``ainvoke`` is never
    touched at all.
    """
    calls = []
    real_ainvoke = deck.workflows.get("TwoStepFlow").build().ainvoke

    async def spy_ainvoke(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_ainvoke(*args, **kwargs)

    monkeypatch.setattr(deck.workflows.get("TwoStepFlow").build(), "ainvoke", spy_ainvoke)

    async with deck:
        out = await deck.run("TwoStepFlow", {"text": "hi"})

    assert out == {"text": "HI", "count": 1}
    assert calls == []  # astream, not ainvoke — the Runtime's own path for every invocable


async def test_agent_node_now_uses_run_streamed_even_via_plain_run(deck, monkeypatch):
    """The invariant this used to guarantee — a plain ``run_workflow()`` call never touches
    ``Runner.run_streamed`` — no longer holds once workflows play on the Runtime: v1's compat
    engine turns nested-agent streaming on unconditionally, because one Runtime run produces
    one canonical stream regardless of whether the caller asked to see it. A caller wanting
    the old cancellation/exception-timing semantics of a bare ``Runner.run`` has none of
    agentdeck's turn-starting methods left to reach for.
    """
    run_calls = []
    run_streamed_calls = []

    async def fake_run(agent, message, **kwargs):
        run_calls.append((agent, message))
        return SimpleNamespace(final_output="Hello!", context_wrapper=SimpleNamespace(usage=None))

    def fake_run_streamed(agent, message, **kwargs):
        run_streamed_calls.append((agent, message))
        return FakeRunResultStreaming(events=[], final_output="Hello!")

    monkeypatch.setattr("agentdeck.authoring.runners.agent.Runner.run", fake_run)
    monkeypatch.setattr("agentdeck.authoring.runners.agent.Runner.run_streamed", fake_run_streamed)

    async with deck:
        out = await deck.run("ChatFlow", {"input": "hi"})

    assert out == {"input": "hi", "output": "Hello!"}
    assert run_calls == []
    assert len(run_streamed_calls) == 1


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


def test_workflow_stream_endpoint_emits_updates_then_done(client):
    """A ``get_stream_writer()`` write is what v1's ``custom`` frame carries, so the workflow
    here makes one: nothing else on the wire distinguishes a rendered ``custom`` event from a
    frame that was never emitted at all."""
    response = client.post("/workflows/WriterFlow?stream=true", json={"text": "hi"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert _sse_frames(response.text) == [
        ("message", {"type": "node_update", "node": "shout", "delta": {"text": "HI"}}),
        ("message", {"type": "custom", "data": "chunk"}),
        ("message", {"type": "node_update", "node": "write", "delta": {"count": 1}}),
        ("done", {"text": "HI", "count": 1}),
    ]


def test_workflow_stream_endpoint_reports_mid_stream_failure(client):
    """The status code is already on the wire, so the failure arrives in-band — as the
    exception's type name, never its message."""
    from agentdeck_project.workflows.writer.workflow import SECRET

    response = client.post("/workflows/HalfwayFlow?stream=true", json={"text": "hi"})

    frames = _sse_frames(response.text)
    assert frames[0] == ("message", {"type": "node_update", "node": "shout", "delta": {"text": "HI"}})
    assert frames[-1] == ("error", {"error": "SkillError"})
    assert SECRET not in response.text


def test_run_workflow_endpoint_unchanged_when_not_streamed(client):
    response = client.post("/workflows/TwoStepFlow", json={"text": "hi"})

    assert response.status_code == 200
    assert response.json() == {"text": "HI", "count": 1}
