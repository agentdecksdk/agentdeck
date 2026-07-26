"""chat_stream / run_streamed / the SSE endpoint: no live model, fakes the SDK boundary."""

import json
import sys
import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agentdeck.agents.runners.headless import HeadlessRunner, StreamDone

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
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    return App()


def _delta_event(text: str) -> SimpleNamespace:
    # Duck-types agents.stream_events.RawResponsesStreamEvent wrapping a
    # ResponseTextDeltaEvent — the only fields run_streamed reads.
    return SimpleNamespace(
        type="raw_response_event",
        data=SimpleNamespace(type="response.output_text.delta", delta=text),
    )


def _other_event() -> SimpleNamespace:
    # A non-text-delta event (tool call, handoff, ...) — must be skipped.
    return SimpleNamespace(type="run_item_stream_event", data=SimpleNamespace(type="tool_called"))


@dataclass
class FakeRunResultStreaming:
    """Duck-types ``agents.result.RunResultStreaming`` for the surface run_streamed uses."""

    events: list
    final_output: str
    cancelled: int = 0
    context_wrapper: object = field(
        default_factory=lambda: SimpleNamespace(
            usage=SimpleNamespace(requests=1, input_tokens=3, output_tokens=4, total_tokens=7)
        )
    )

    async def stream_events(self):
        for event in self.events:
            yield event

    def cancel(self, mode="immediate"):
        self.cancelled += 1


async def test_run_streamed_yields_deltas_incrementally(project, monkeypatch):
    agent_cls = project.agents.get("Greeter")
    runner = HeadlessRunner.from_agent(agent_cls.build())

    events = [_delta_event("Hel"), _other_event(), _delta_event("lo"), _delta_event("!")]
    fake_result = FakeRunResultStreaming(events=events, final_output="Hello!")
    captured_kwargs = {}

    def fake_run_streamed(agent, message, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr("agentdeck.agents.runners.headless.Runner.run_streamed", fake_run_streamed)

    sentinel_session = object()
    chunks = [c async for c in runner.run_streamed("hi", session=sentinel_session)]

    assert chunks[:-1] == ["Hel", "lo", "!"]
    # The turn ends with the SDK's own final_output + usage, not the re-joined deltas.
    assert chunks[-1] == StreamDone(
        final_output="Hello!",
        usage={"requests": 1, "input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
    )
    # The detached SDK run loop is always cancelled once the generator is done with it.
    assert fake_result.cancelled == 1
    # run_config / max_turns / session are threaded through exactly like HeadlessRunner.run.
    assert captured_kwargs["run_config"] is runner.run_config
    assert captured_kwargs["max_turns"] == runner.max_turns
    assert captured_kwargs["session"] is sentinel_session


async def test_run_streamed_cancels_sdk_run_on_abandonment(project, monkeypatch):
    """A caller that stops mid-stream (client disconnect) must not leave the run loop alive."""
    agent_cls = project.agents.get("Greeter")
    runner = HeadlessRunner.from_agent(agent_cls.build())

    fake_result = FakeRunResultStreaming(events=[_delta_event("a"), _delta_event("b")], final_output="ab")
    monkeypatch.setattr(
        "agentdeck.agents.runners.headless.Runner.run_streamed",
        lambda agent, message, **kwargs: fake_result,
    )

    stream = runner.run_streamed("hi")
    assert await anext(stream) == "a"
    await stream.aclose()

    assert fake_result.cancelled == 1


async def test_chat_unchanged_when_not_streamed(project, monkeypatch):
    calls = []

    class StubRunner:
        def __init__(self, session_factory):
            self._session_factory = session_factory

        async def run(self, message, *, session=None):
            calls.append(("run", session))
            return SimpleNamespace(final_output=f"echo:{message}")

        async def run_streamed(self, message, *, session=None):
            calls.append(("run_streamed", session))
            for chunk in f"echo:{message}":
                yield chunk

    monkeypatch.setattr(
        "agentdeck.app.HeadlessRunner.from_agent",
        classmethod(lambda cls, agent, **_: StubRunner(None)),
    )

    result = await project.chat("Greeter", "s1", "hi")
    assert result.final_output == "echo:hi"
    assert calls == [("run", project.session_for("s1"))]


async def test_chat_stream_uses_same_session_as_chat(project, monkeypatch):
    seen_sessions = []

    class StubRunner:
        async def run(self, message, *, session=None):
            seen_sessions.append(("run", session))
            return SimpleNamespace(final_output=f"echo:{message}")

        async def run_streamed(self, message, *, session=None):
            seen_sessions.append(("run_streamed", session))
            for chunk in f"echo:{message}":
                yield chunk

    monkeypatch.setattr(
        "agentdeck.app.HeadlessRunner.from_agent",
        classmethod(lambda cls, agent, **_: StubRunner()),
    )

    deltas = [d async for d in project.chat_stream("Greeter", "s1", "hi")]
    result = await project.chat("Greeter", "s1", "hi")

    assert "".join(deltas) == "echo:hi" == result.final_output
    # Both paths resolve session_for("s1") — same object — so history stays identical
    # whether the turn was streamed or not.
    run_streamed_session = seen_sessions[0][1]
    run_session = seen_sessions[1][1]
    assert run_streamed_session is run_session is project.session_for("s1")


def _sse_frames(text: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into ``(event_name, data)`` pairs; unnamed frames are "message"."""
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
    """TestClient over the real FastAPI app; only ``App.chat_stream`` is stubbed per test."""
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    # context manager runs the lifespan; without it every endpoint is 503
    with TestClient(create_app()) as c:
        yield c


def test_stream_endpoint_emits_deltas_then_done(client, monkeypatch):
    async def fake_chat_stream(self, name, session_id, message, **_):
        for delta in ("Hel", "lo"):
            yield delta
        yield StreamDone(final_output={"greeting": "Hello"}, usage={"total_tokens": 7})

    monkeypatch.setattr("agentdeck.app.App.chat_stream", fake_chat_stream)

    response = client.post("/agents/Greeter/chat?stream=true", json={"session_id": "s1", "message": "hi"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert _sse_frames(response.text) == [
        ("message", {"delta": "Hel"}),
        ("message", {"delta": "lo"}),
        # done carries the SDK's final_output (a validated model here), not "Hello".
        ("done", {"output": {"greeting": "Hello"}, "usage": {"total_tokens": 7}}),
    ]


def test_stream_endpoint_rejects_missing_session_id(client):
    response = client.post("/agents/Greeter/chat?stream=true", json={"message": "hi"})

    # 4xx before any header is sent — not a 200 that streams nothing.
    assert response.status_code == 422
    assert "session_id" in response.json()["detail"]


def test_stream_endpoint_reports_mid_stream_failure(client, monkeypatch):
    async def exploding_chat_stream(self, name, session_id, message, **_):
        yield "par"
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("agentdeck.app.App.chat_stream", exploding_chat_stream)

    response = client.post("/agents/Greeter/chat?stream=true", json={"session_id": "s1", "message": "hi"})

    frames = _sse_frames(response.text)
    assert frames[0] == ("message", {"delta": "par"})
    assert frames[-1] == ("error", {"error": "RuntimeError"})
    assert "secret internal detail" not in response.text
