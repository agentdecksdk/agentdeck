"""What the openai-agents engine hands the SDK, and what it reports back.

Issue #61: it must not enable the SDK's default trace exporter on a keyless/fake-model run
unless explicitly opted in via ``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED``. Issue #101: an
``output_type`` agent's validated result travels as a ``DataBlock`` on ``run.completed``
instead of failing the run.

Patches ``Runner.run_streamed`` itself (rather than driving a real fake model through a
full run) so the assertion is exactly on what the engine hands the SDK and what it makes of
the result, regardless of how the stream plays out.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from agents import Agent, RunConfig
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from pydantic import BaseModel, ConfigDict

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents import engine as engine_module
from agentdeck.adapters.engines.openai_agents.engine import _to_sdk_input
from agentdeck.core.content import (
    AudioBlock,
    DataBlock,
    ImageBlock,
    ResourceBlock,
    TextBlock,
    UnknownBlock,
    coerce_input,
)
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunCompleted, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import ConfigError


class _NeverCalledModel(Model):
    """The fake ``Runner.run_streamed`` below never touches the model; this just satisfies
    ``Agent``'s constructor."""

    async def stream_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError
        yield  # pragma: no cover — makes this an async generator; never reached

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError


class _FakeStreamResult:
    """Stands in for ``RunResultStreaming``: an immediately-empty event stream."""

    def __init__(self, final_output: Any = "ok") -> None:
        self.final_output = final_output
        self.context_wrapper = type("ContextWrapper", (), {"usage": None})()

    async def stream_events(self) -> Any:
        return
        yield  # pragma: no cover — makes this an async generator; never reached

    def cancel(self) -> None:
        pass


def _spec() -> InvocableSpec:
    agent = Agent(name="Test", instructions="reply", model=_NeverCalledModel())
    return InvocableSpec(name="Test", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def _ctx() -> RunContext:
    return RunContext(namespace="acme", run_id="r-1", session_id="s-1")


async def _run_config_passed_to_runner(monkeypatch: pytest.MonkeyPatch) -> RunConfig:
    captured: dict[str, Any] = {}

    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, run_config: RunConfig | None = None, **_kwargs: Any) -> _FakeStreamResult:
            captured["run_config"] = run_config
            return _FakeStreamResult()

    monkeypatch.setattr(engine_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())
    async for _ in engine.start(_spec(), coerce_input("hi"), [], _ctx()):
        pass
    return captured["run_config"]


async def test_start_disables_tracing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", raising=False)
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config is not None
    assert run_config.tracing_disabled is True


async def test_start_keeps_tracing_off_for_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "false")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is True


async def test_start_enables_tracing_on_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "true")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is False


# --- the final output (issue #101) ------------------------------------------------------


class _Slot(BaseModel):
    day: str
    hour: int


@dataclasses.dataclass
class _Decision:
    approved: bool


async def _terminal(monkeypatch: pytest.MonkeyPatch, final_output: Any) -> RunCompleted:
    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FakeStreamResult:
            return _FakeStreamResult(final_output)

    monkeypatch.setattr(engine_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())
    payloads = [payload async for payload in engine.start(_spec(), coerce_input("hi"), [], _ctx())]
    terminal = payloads[-1]
    assert isinstance(terminal, RunCompleted)
    return terminal


async def test_a_text_final_output_is_a_text_block(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = await _terminal(monkeypatch, "ok")
    assert terminal.output == [TextBlock(text="ok")]


async def test_a_validated_output_type_result_is_a_data_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """It used to raise, which turned a documented feature into a failed run."""
    terminal = await _terminal(monkeypatch, _Slot(day="tuesday", hour=9))
    assert terminal == RunCompleted(
        output=[DataBlock(data={"day": "tuesday", "hour": 9})],
        usage=Usage(input_tokens=0, output_tokens=0),
    )


async def test_a_dataclass_output_type_result_is_a_data_block(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = await _terminal(monkeypatch, _Decision(approved=True))
    assert terminal.output == [DataBlock(data={"approved": True})]


async def test_an_output_the_sdk_cannot_json_becomes_its_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """The declared ceiling: a run reports its answer rather than failing at its last event."""
    terminal = await _terminal(monkeypatch, _NeverCalledModel)
    assert terminal.output == [DataBlock(data=str(_NeverCalledModel))]


async def test_a_validated_result_with_an_unrenderable_leaf_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling covers the pydantic branch too: ``model_dump(mode="json")`` raises on a leaf
    it cannot render, and that raise is what would kill the run at ``run.completed``."""

    class _Mixed(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        day: str
        opaque: Any

    opaque = object()
    terminal = await _terminal(monkeypatch, _Mixed(day="tuesday", opaque=opaque))
    assert terminal.output == [DataBlock(data={"day": "tuesday", "opaque": str(opaque)})]


async def test_a_validated_result_keeps_json_fidelity_for_the_leaves_pydantic_knows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why the pydantic branch stays on ``mode="json"`` instead of dumping in python mode: a
    ``datetime`` leaf is ISO-8601 there, and ``str()`` of one is not."""

    class _Booking(BaseModel):
        at: datetime

    terminal = await _terminal(monkeypatch, _Booking(at=datetime(2026, 1, 6, 9, 0, tzinfo=UTC)))
    assert terminal.output == [DataBlock(data={"at": "2026-01-06T09:00:00Z"})]


async def test_a_non_finite_float_in_a_result_becomes_its_token_not_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NaN`` is refused by ``DataBlock`` (it would serialize as ``null``), so the adapter
    degrades it under its own ceiling rather than failing the run."""

    class _Score(BaseModel):
        ratio: float

    terminal = await _terminal(monkeypatch, _Score(ratio=float("nan")))
    assert terminal.output == [DataBlock(data={"ratio": "NaN"})]


# --- multimodal input (#161) -------------------------------------------------------------


def test_all_text_input_still_returns_the_joined_str_not_a_list():
    """The common path takes no new shape: unchanged from before #161, byte for byte."""
    result = _to_sdk_input([TextBlock(text="a"), TextBlock(text="b")], use_responses=True)
    assert result == "a\nb"
    assert isinstance(result, str)


def test_text_and_image_produce_one_item_with_ordered_parts():
    blocks = [TextBlock(text="look"), ImageBlock(media_type="image/png", data_b64="AAAA")]
    assert _to_sdk_input(blocks, use_responses=True) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        }
    ]


def test_two_text_blocks_around_an_image_are_two_separate_parts_not_one_joined_part():
    blocks = [TextBlock(text="a"), ImageBlock(media_type="image/png", data_b64="AA=="), TextBlock(text="b")]
    parts = _to_sdk_input(blocks, use_responses=True)[0]["content"]
    assert parts == [
        {"type": "input_text", "text": "a"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
        {"type": "input_text", "text": "b"},
    ]


def test_audio_strips_media_type_parameters_into_the_format():
    """WhatsApp voice notes arrive as ``audio/ogg; codecs=opus``; the format openai's own
    converter wants is ``ogg``, unvalidated against its ``Literal["mp3", "wav"]``."""
    block = AudioBlock(media_type="audio/ogg; codecs=opus", data_b64="AAAA")
    assert _to_sdk_input([block], use_responses=False) == [
        {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": "AAAA", "format": "ogg"}}]}
    ]


def test_audio_under_responses_raises_naming_the_responses_api():
    block = AudioBlock(media_type="audio/wav", data_b64="AAAA")
    with pytest.raises(ConfigError, match="Responses API"):
        _to_sdk_input([block], use_responses=True)


def test_the_same_audio_block_under_chat_completions_converts():
    block = AudioBlock(media_type="audio/wav", data_b64="AAAA")
    result = _to_sdk_input([block], use_responses=False)
    assert result[0]["content"][0] == {"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}


@pytest.mark.parametrize(
    "block",
    [
        ResourceBlock(uri="s3://bucket/key"),
        DataBlock(data={"a": 1}),
        UnknownBlock(type="video", raw_block={"type": "video"}),
    ],
)
def test_unsupported_blocks_raise_naming_the_block_and_the_engine(block):
    with pytest.raises(ConfigError, match="openai-agents engine"):
        _to_sdk_input([TextBlock(text="hi"), block], use_responses=True)


def test_the_sdk_converter_is_the_oracle_for_the_emitted_shape():
    """``Converter.items_to_messages`` is the SDK's own chat-completions converter — feeding it
    what ``_to_sdk_input`` emits proves the shape agentdeck writes is the shape the SDK expects,
    without agentdeck writing a converter of its own."""
    blocks = [
        TextBlock(text="look"),
        ImageBlock(media_type="image/png", data_b64="AAAA"),
        AudioBlock(media_type="audio/ogg; codecs=opus", data_b64="BBBB"),
    ]
    items = _to_sdk_input(blocks, use_responses=False)
    messages = Converter.items_to_messages(items)
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "auto"}},
                {"type": "input_audio", "input_audio": {"data": "BBBB", "format": "ogg"}},
            ],
        }
    ]


async def test_start_hands_the_sdk_boundary_the_multimodal_item_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not just a unit test of ``_to_sdk_input`` in isolation — the exact value ``engine.start``
    passes ``Runner.run_streamed`` as its ``message`` argument."""
    captured: dict[str, Any] = {}

    class _FakeRunner:
        @staticmethod
        def run_streamed(*args: Any, **_kwargs: Any) -> _FakeStreamResult:
            captured["message"] = args[1]
            return _FakeStreamResult()

    monkeypatch.setattr(engine_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())
    blocks = [TextBlock(text="what is this?"), ImageBlock(media_type="image/png", data_b64="AAAA")]
    async for _ in engine.start(_spec(), blocks, [], _ctx()):
        pass

    assert captured["message"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "what is this?"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        }
    ]


async def test_start_raises_for_audio_under_responses_before_touching_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NeverCalledRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FakeStreamResult:
            raise AssertionError("must not reach the SDK boundary")

    monkeypatch.setattr(engine_module, "Runner", _NeverCalledRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())  # default settings: use_responses=True
    with pytest.raises(ConfigError, match="Responses API"):
        async for _ in engine.start(_spec(), [AudioBlock(media_type="audio/wav", data_b64="AAAA")], [], _ctx()):
            pass
