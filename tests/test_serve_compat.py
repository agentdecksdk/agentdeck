"""The compat facade: v1's chat wire, rendered from the canonical events of a real run.

Every case here drives the whole path — v1's resolved run config, the compat engine, the
Runtime, the surface's renderer — with only the model scripted, so the frames asserted
below are the ones the endpoint puts on the wire.
"""

import asyncio
import json
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel, provider_of

from agentdeck.adapters.engines.openai_agents import compat as engine_compat
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.composition import build_runtime, v1_engines
from agentdeck.core.content import coerce_input
from agentdeck.runtime.settings import reset_settings_cache
from agentdeck.serve import create_app
from agentdeck.surfaces.serve import compat as surface_compat
from agentdeck.surfaces.serve.compat import chat_frames, chat_result, run_context

AGENT_PY = """
from pydantic import BaseModel

from agentdeck.agents import BaseAgent


class Greeting(BaseModel):
    greeting: str


class Greeter(BaseAgent):
    instructions = "Greet the user."


class Structured(BaseAgent):
    instructions = "Answer as JSON."
    output_type = Greeting
"""

WORKFLOW_PY = """
from typing import TypedDict

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(TypedDict, total=False):
    input: str


class Shout(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"input": s["input"].upper()})
        g.set_entry_point("shout")
        g.add_edge("shout", END)
        return g
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(textwrap.dedent(WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    return tmp_path


@pytest.fixture
def scripted(monkeypatch):
    """Patch v1's provider and hand back a (runtime, store, model) triple over the project."""

    def _build(model=None):
        model = model or ScriptedModel(deltas=("Hello",))
        monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(model))
        store = MemoryEventStore()
        return build_runtime(engines=v1_engines(), store=store), store, model

    return _build


def test_the_surface_and_the_engine_agree_on_the_structured_output_carrier():
    """The surface spells the engine's custom-event name out rather than importing the
    adapter, so this is what keeps the two from drifting apart."""
    assert surface_compat.STRUCTURED_OUTPUT == engine_compat.STRUCTURED_OUTPUT


async def test_chat_frames_render_deltas_then_done_from_canonical_events(project, scripted):
    runtime, store, _ = scripted(ScriptedModel(deltas=("Tuesday ", "at 9am"), input_tokens=11, output_tokens=5))
    ctx = run_context("s1")

    frames = [frame async for frame in chat_frames(runtime.run("Greeter", coerce_input("when?"), ctx))]

    assert frames == [
        'data: {"delta": "Tuesday "}\n\n',
        'data: {"delta": "at 9am"}\n\n',
        'event: done\ndata: {"output": "Tuesday at 9am", "usage": '
        '{"requests": 1, "input_tokens": 11, "output_tokens": 5, "total_tokens": 16}}\n\n',
    ]
    # The wire is v1's; the log is canonical — the whole point of translating at the surface.
    logged = [event.kind for event in await store.read("s1", ctx)]
    assert logged == [
        "run.started",
        "text.delta",
        "text.delta",
        "usage.reported",
        "message.completed",
        "run.completed",
    ]


async def test_a_failed_turn_ends_with_an_error_frame_while_the_log_keeps_the_failure(project, scripted):
    runtime, store, _ = scripted(ScriptedModel(deltas=("par",), raises=RuntimeError("secret detail")))
    ctx = run_context("s1")

    frames = [frame async for frame in chat_frames(runtime.run("Greeter", coerce_input("hi"), ctx))]

    assert frames[0] == 'data: {"delta": "par"}\n\n'
    assert frames[-1] == 'event: error\ndata: {"error": "RuntimeError"}\n\n'
    assert "secret detail" not in "".join(frames)
    # v1's wire has no frame for a recorded failure, but the log must still hold one.
    assert [event.kind for event in await store.read("s1", ctx)][-1] == "run.failed"


async def test_chat_result_returns_v1s_output_body(project, scripted):
    runtime, _, _ = scripted(ScriptedModel(deltas=("Hello",)))

    body = await chat_result(runtime.run("Greeter", coerce_input("hi"), run_context("s1")))

    assert body == {"output": "Hello"}


async def test_a_structured_output_survives_the_canonical_stream(project, scripted):
    """``RunCompleted.output`` can only hold text, so the engine carries an ``output_type``
    result alongside it and the surface renders that instead."""
    runtime, store, _ = scripted(ScriptedModel(deltas=('{"greeting": "Hello"}',)))
    ctx = run_context("s1")

    body = await chat_result(runtime.run("Structured", coerce_input("hi"), ctx))

    assert body == {"output": {"greeting": "Hello"}}
    assert surface_compat.STRUCTURED_OUTPUT in [
        event.payload.name for event in await store.read("s1", ctx) if event.kind == "custom"
    ]


async def test_a_structured_output_reaches_the_streamed_done_frame(project, scripted):
    runtime, _, _ = scripted(ScriptedModel(deltas=('{"greeting": "Hello"}',), input_tokens=1, output_tokens=2))

    frames = [frame async for frame in chat_frames(runtime.run("Structured", coerce_input("hi"), run_context("s1")))]

    assert frames[-1] == (
        'event: done\ndata: {"output": {"greeting": "Hello"}, "usage": '
        '{"requests": 1, "input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}\n\n'
    )


def test_the_endpoint_answers_a_structured_agent_with_its_object(project, monkeypatch):
    monkeypatch.setattr(
        "agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel(deltas=('{"greeting": "Hi"}',)))
    )

    with TestClient(create_app()) as client:
        response = client.post("/agents/Structured/chat", json={"session_id": "s1", "message": "hi"})

    assert response.status_code == 200
    assert response.json() == {"output": {"greeting": "Hi"}}


def test_the_endpoint_logs_its_run_to_the_configured_event_store(project, monkeypatch, tmp_path):
    """The wire is v1's, so this is what tells the two apart: a chat served by the Runtime
    leaves its canonical run in the store the composition root resolved from settings."""
    db = tmp_path / "events.sqlite3"
    monkeypatch.setenv("AGENTDECK_EVENTS_BACKEND", "sqlite")
    monkeypatch.setenv("AGENTDECK_EVENTS_URL", str(db))
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel()))
    reset_settings_cache()
    try:
        with TestClient(create_app()) as client:
            assert client.post("/agents/Greeter/chat", json={"session_id": "s1", "message": "hi"}).status_code == 200
    finally:
        reset_settings_cache()

    store = SqliteEventStore(str(db))
    ctx = run_context("s1")
    kinds = [event.kind for event in asyncio.run(store.read("s1", ctx))]
    store.close()
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"


def test_a_workflow_is_not_reachable_through_the_agents_route(project, monkeypatch):
    """The Runtime knows every invocable; this route is still agents-only, with v1's message."""
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel()))

    with TestClient(create_app()) as client:
        response = client.post("/agents/Shout/chat", json={"session_id": "s1", "message": "hi"})

    assert response.status_code == 404
    assert response.json()["detail"].startswith("No agent named 'Shout'.")


async def test_the_runtime_and_the_python_api_share_one_conversation(project, monkeypatch):
    """v1 kept one session per id whichever entry point ran the turn; the compat engine
    takes v1's own session lookup so that stays true across the two paths."""
    from agentdeck import App

    model = ScriptedModel(deltas=("Hello",))
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(model))
    app = App()
    app.load()

    async for _ in app.runtime.run("Greeter", coerce_input("over http"), run_context("s1")):
        pass
    await app.chat("Greeter", "s1", "over python")

    # the Python API's turn opened on the Runtime turn's message, so both wrote one session
    assert json.dumps(model.inputs[0], default=str).count("over") == 1
    assert "over http" in json.dumps(model.inputs[-1], default=str)
    await app.aclose()
