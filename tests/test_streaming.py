"""chat_stream / HeadlessRunner.run_streamed: no live model, fakes the SDK boundary."""

import sys
import textwrap
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from agentdeck.agents.runners.headless import HeadlessRunner

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
    """Duck-types ``agents.result.RunResultStreaming`` for the one surface used."""

    events: list
    final_output: str

    async def stream_events(self):
        for event in self.events:
            yield event


@pytest.mark.asyncio
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
    deltas = [d async for d in runner.run_streamed("hi", session=sentinel_session)]

    assert deltas == ["Hel", "lo", "!"]
    # run_config / max_turns / session are threaded through exactly like HeadlessRunner.run.
    assert captured_kwargs["run_config"] is runner.run_config
    assert captured_kwargs["max_turns"] == runner.max_turns
    assert captured_kwargs["session"] is sentinel_session


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
