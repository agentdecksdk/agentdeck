"""chat_stream / run_streamed / the SSE endpoint: no live model, fakes the SDK boundary."""

import json
import textwrap
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agentdeck.authoring.runners.agent import HeadlessRunner, StreamDone
from agentdeck.testing import ScriptedModel, patch_model

AGENT_PY = """
from agents import function_tool

from agentdeck.authoring import Agent


@function_tool
def lookup_slot(day: str) -> str:
    "Return the fixed free slot for a day."
    return f"{day} 09:00"


greeter = Agent(name="Greeter", instructions="Greet the user.")
tooler = Agent(name="Tooler", instructions="Use the tool, then answer.", tools=[lookup_slot])
"""


def _usage_frame(requests: int, input_tokens: int, output_tokens: int) -> dict[str, int]:
    """v1's aggregate usage dict, in v1's key order."""
    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The project directory only  -  a deck of this suite's own and the server's are two
    different decks, and one Deck holds the process at a time."""
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
async def deck(project):  # noqa: ARG001  -  the project dir is what `from_project()` discovers
    from agentdeck.deck import Deck

    deck = Deck.from_project()
    yield deck
    await deck.aclose()


def _delta_event(text: str) -> SimpleNamespace:
    # Duck-types agents.stream_events.RawResponsesStreamEvent wrapping a
    # ResponseTextDeltaEvent  -  the only fields run_streamed reads.
    return SimpleNamespace(
        type="raw_response_event",
        data=SimpleNamespace(type="response.output_text.delta", delta=text),
    )


def _other_event() -> SimpleNamespace:
    # A non-text-delta event (tool call, handoff, ...)  -  must be skipped.
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


async def test_run_streamed_yields_deltas_incrementally(deck, monkeypatch):
    agent_cls = deck.agents.get("Greeter")
    runner = HeadlessRunner.from_agent(agent_cls.build())

    events = [_delta_event("Hel"), _other_event(), _delta_event("lo"), _delta_event("!")]
    fake_result = FakeRunResultStreaming(events=events, final_output="Hello!")
    captured_kwargs = {}

    def fake_run_streamed(agent, message, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr("agentdeck.authoring.runners.agent.Runner.run_streamed", fake_run_streamed)

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


async def test_run_streamed_cancels_sdk_run_on_abandonment(deck, monkeypatch):
    """A caller that stops mid-stream (client disconnect) must not leave the run loop alive."""
    agent_cls = deck.agents.get("Greeter")
    runner = HeadlessRunner.from_agent(agent_cls.build())

    fake_result = FakeRunResultStreaming(events=[_delta_event("a"), _delta_event("b")], final_output="ab")
    monkeypatch.setattr(
        "agentdeck.authoring.runners.agent.Runner.run_streamed",
        lambda agent, message, **kwargs: fake_result,
    )

    stream = runner.run_streamed("hi")
    assert await anext(stream) == "a"
    await stream.aclose()

    assert fake_result.cancelled == 1


async def test_run_returns_a_turn_result_not_the_sdks_runresult(deck):
    """``run`` used to hand back the SDK's own ``RunResult``; it now plays on the Runtime
    and returns a :class:`~agentdeck.deck.TurnResult` assembled from the run's
    own ``run.completed``  -  a caller depends on agentdeck's event schema, never on the SDK.
    """
    with patch_model(ScriptedModel(deltas=("echo:hi",))):
        async with deck:
            result = await deck.run("Greeter", "hi", session_id="s1")

    assert result.output == "echo:hi"
    assert result.session_id == "s1"


async def test_stream_uses_same_session_as_run(deck):
    """One ``session_id`` is one conversation whichever Deck method ran the turn  -  the same
    guarantee the old ``HeadlessRunner``-backed methods gave, now proven at the SDK boundary
    instead of by stubbing ``HeadlessRunner.from_agent`` directly (which ``run``/``stream``
    no longer call: both play on the Runtime)."""
    from agentdeck.core.events import RunCompleted

    model = ScriptedModel(deltas=("echo:hi",))

    with patch_model(model):
        async with deck:
            events = [event async for event in deck.stream("Greeter", "first", session_id="s1")]
            result = await deck.run("Greeter", "second", session_id="s1")

    streamed_output = next(e.payload.output[0].text for e in events if isinstance(e.payload, RunCompleted))
    assert streamed_output == "echo:hi" == result.output
    # two model calls, and the second turn's input carries the first turn's own message  -
    # proof the two Deck methods shared one `session_for("s1")` rather than each starting fresh.
    assert model.calls == 2
    assert "first" in str(model.inputs[-1])


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
