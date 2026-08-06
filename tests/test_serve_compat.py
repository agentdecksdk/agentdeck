"""The compat facade: v1's chat wire, rendered from the canonical events of a real run.

Every case here drives the whole path — v1's resolved run config, the compat engine, the
Runtime, the surface's renderer — with only the model scripted, so the frames asserted
below are the ones the endpoint puts on the wire.
"""

import asyncio
import json
import sys
import textwrap
from contextlib import aclosing, contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedModel, provider_of

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.compat import engine as compat_engine
from agentdeck.composition import build_runtime, v1_engines
from agentdeck.core.content import coerce_input
from agentdeck.core.events import check_terminal
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


class RecordingTrace:
    """Stands in for the Langfuse observation v1 opens around a turn, recording what the run
    reported about itself — the difference between a trace that reads as succeeded and one that
    reads as errored, without needing the ``[observability]`` extra installed."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str | None]] = []

    def set_output(self, output: Any = None, *, error: str | None = None) -> None:
        self.calls.append((output, error))


@pytest.fixture
def recorded_trace(monkeypatch):
    """Swap v1's Langfuse observation for one that records what the run reported into it."""
    trace = RecordingTrace()

    @contextmanager
    def _trace_run(_capture, **_kwargs):
        yield trace

    monkeypatch.setattr("agentdeck.compat.engine.trace_run", _trace_run)
    return trace


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
    """The surface spells the engine's custom-event name out rather than importing it, so this
    is what keeps the two from drifting apart."""
    assert surface_compat.STRUCTURED_OUTPUT == compat_engine.STRUCTURED_OUTPUT


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


async def test_a_completed_turn_reports_its_output_to_the_trace_not_a_failure(project, scripted, recorded_trace):
    """A successful run ends by the Runtime closing the engine's generator, which is not an
    abandoned run: the trace must carry the output and no error, or every chat turn shows up
    in Langfuse as errored."""
    runtime, _, _ = scripted(ScriptedModel(deltas=("Hello",)))

    frames = [frame async for frame in chat_frames(runtime.run("Greeter", coerce_input("hi"), run_context("s1")))]

    assert frames[-1].startswith("event: done")
    assert recorded_trace.calls == [("Hello", None)]


async def test_an_abandoned_turn_reports_the_abandonment_to_the_trace(project, scripted, recorded_trace):
    """Walking away before the engine reached its terminal event is the one case that *is* a
    failed observation. Deterministic without a sleep: the engine says whether it finished,
    rather than the test racing the SDK's detached run loop."""
    runtime, _, _ = scripted(ScriptedModel(deltas=("one", "two", "three")))

    events = runtime.run("Greeter", coerce_input("hi"), run_context("s1"))
    async with aclosing(chat_frames(events)) as frames:
        await anext(frames)  # one delta, then walk away mid-run

    assert [error for _, error in recorded_trace.calls] == ["GeneratorExit: consumer stopped reading"]


async def test_an_abandoned_stream_closes_its_run_in_the_log(project, scripted):
    """An ASGI server abandons a response body without closing it, so the surface has to
    close the run itself — an unterminated run in the log is indistinguishable from one still
    in flight."""
    runtime, store, _ = scripted(ScriptedModel(deltas=("one", "two", "three")))
    ctx = run_context("s1")

    events = runtime.run("Greeter", coerce_input("hi"), ctx)
    async with aclosing(chat_frames(events)) as frames:
        await anext(frames)

    logged = [event.kind for event in await store.read("s1", ctx)]
    assert logged[-1] == "run.cancelled", logged
    assert check_terminal(await store.read("s1", ctx)) is None


async def test_the_done_output_is_the_sdks_final_output_not_the_rejoined_deltas(project, scripted):
    """v1's ``done`` carried the SDK's own ``final_output``, which disagrees with the deltas
    for a tool-using or output-shaping agent."""
    runtime, _, _ = scripted(ScriptedModel(deltas=("Hel", "lo"), final_text="Hello, from the SDK."))

    frames = [frame async for frame in chat_frames(runtime.run("Greeter", coerce_input("hi"), run_context("s1")))]

    assert '"output": "Hello, from the SDK."' in frames[-1]


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


@pytest.mark.parametrize("query", ["", "?stream=true"], ids=["body", "streamed"])
def test_the_endpoint_logs_its_run_to_the_configured_event_store(project, monkeypatch, tmp_path, query):
    """The wire is v1's, so this is what tells the two apart: a chat served by the Runtime
    leaves its canonical run in the store the composition root resolved from settings. Both
    endpoints are checked — one of them silently falling back to v1 glue would otherwise pass
    every other test in the suite, goldens included."""
    db = tmp_path / "events.sqlite3"
    monkeypatch.setenv("AGENTDECK_EVENTS_BACKEND", "sqlite")
    monkeypatch.setenv("AGENTDECK_EVENTS_URL", str(db))
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel()))
    reset_settings_cache()
    try:
        with TestClient(create_app()) as client:
            response = client.post(f"/agents/Greeter/chat{query}", json={"session_id": "s1", "message": "hi"})
            assert response.status_code == 200
            assert response.text  # the streamed body is only produced while the client reads it
    finally:
        reset_settings_cache()

    store = SqliteEventStore(str(db))
    ctx = run_context("s1")
    kinds = [event.kind for event in asyncio.run(store.read("s1", ctx))]
    store.close()
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"


@pytest.mark.parametrize("query", ["", "?stream=true"], ids=["body", "streamed"])
@pytest.mark.parametrize(
    "message",
    [{"role": "user", "content": "hi"}, [{"role": "user", "content": "hi"}], 7],
    ids=["object", "input-items", "number"],
)
def test_a_message_that_is_not_a_string_is_a_422(project, monkeypatch, message, query):
    """A shape the endpoint cannot run is a client error with a body like every other one it
    emits — never an unhandled server exception in somebody's 5xx alerting."""
    monkeypatch.setattr("agentdeck.agents.runners.base.OpenAIProvider", provider_of(ScriptedModel()))

    with TestClient(create_app()) as client:
        response = client.post(f"/agents/Greeter/chat{query}", json={"session_id": "s1", "message": message})

    assert response.status_code == 422
    assert response.json()["detail"].startswith("message must be a string")


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
