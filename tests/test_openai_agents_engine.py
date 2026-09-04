"""What the openai-agents engine hands the SDK, and what it reports back.

Issue #61: it must not enable the SDK's default trace exporter on a keyless/fake-model run
unless explicitly opted in via ``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED``. Issue #101: an
``output_type`` agent's validated result travels as a ``DataBlock`` on ``run.completed``
instead of failing the run. Issue #226: a ``DataBlock`` on *input* renders as JSON text instead
of being refused; ``ResourceBlock`` still is. Issue #636: the artifacts a finished run left in
``new_items`` lead its ``run.completed`` output, and its final output closes it.

Patches ``Runner.run_streamed`` itself (rather than driving a real fake model through a
full run) so the assertion is exactly on what the engine hands the SDK and what it makes of
the result, regardless of how the stream plays out.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from agents import Agent, RunConfig
from agents.items import MessageOutputItem, ToolCallItem, ToolCallOutputItem
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from agents.tool import ToolOutputFileContent, ToolOutputImage, ToolOutputText
from openai import AuthenticationError
from openai.types.responses import ResponseOutputMessage, ResponseOutputText
from openai.types.responses.response_output_item import ImageGenerationCall
from pydantic import BaseModel, ConfigDict

from agentdeck.adapters.executors.openai_agents import ExecutionStore, OpenAIAgentsExecutor
from agentdeck.adapters.executors.openai_agents import executor as executor_module
from agentdeck.adapters.executors.openai_agents.executor import _to_sdk_input
from agentdeck.adapters.executors.openai_agents.translate import run_artifacts
from agentdeck.core.content import (
    INLINE_BYTES_CAP,
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

if TYPE_CHECKING:
    from collections.abc import Sequence


class _NeverCalledModel(Model):
    """The fake ``Runner.run_streamed`` below never touches the model; this just satisfies
    ``Agent``'s constructor."""

    async def stream_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError
        yield  # pragma: no cover  -  makes this an async generator; never reached

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError


class _FakeStreamResult:
    """Stands in for ``RunResultStreaming``: an immediately-empty event stream."""

    def __init__(self, final_output: Any = "ok", new_items: Sequence[Any] = ()) -> None:
        self.final_output = final_output
        self.new_items = new_items
        self.context_wrapper = type("ContextWrapper", (), {"usage": None})()

    async def stream_events(self) -> Any:
        return
        yield  # pragma: no cover  -  makes this an async generator; never reached

    def cancel(self) -> None:
        pass


def _spec() -> InvocableSpec:
    agent = Agent(name="Test", instructions="reply", model=_NeverCalledModel())
    return InvocableSpec(name="Test", kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=agent)


def _ctx() -> RunContext:
    return RunContext(namespace="acme", run_id="r-1", session_id="s-1")


async def _run_config_passed_to_runner(monkeypatch: pytest.MonkeyPatch) -> RunConfig:
    captured: dict[str, Any] = {}

    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, run_config: RunConfig | None = None, **_kwargs: Any) -> _FakeStreamResult:
            captured["run_config"] = run_config
            return _FakeStreamResult()

    monkeypatch.setattr(executor_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsExecutor(ExecutionStore())
    async for _ in engine.execute(_spec(), coerce_input("hi"), [], _ctx()):
        pass
    return captured["run_config"]


async def test_execute_disables_tracing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", raising=False)
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config is not None
    assert run_config.tracing_disabled is True


async def test_execute_keeps_tracing_off_for_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "false")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is True


async def test_execute_enables_tracing_on_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "true")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is False


# --- a provider auth failure surfaces unwrapped (issue #519) ----------------------------


async def test_a_provider_auth_failure_surfaces_as_the_sdks_own_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deck.build() no longer gates on credentials; a real auth failure must still reach the
    caller as the provider/SDK's own exception, not a build-time or rewritten one."""
    sentinel = AuthenticationError(
        "invalid api key",
        response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/chat/completions")),
        body=None,
    )

    class _FailingStreamResult(_FakeStreamResult):
        async def stream_events(self) -> Any:
            raise sentinel
            yield  # pragma: no cover  -  makes this an async generator; never reached

    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FailingStreamResult:
            return _FailingStreamResult()

    monkeypatch.setattr(executor_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsExecutor(ExecutionStore())

    with pytest.raises(AuthenticationError) as exc_info:
        async for _ in engine.execute(_spec(), coerce_input("hi"), [], _ctx()):
            pass
    assert exc_info.value is sentinel


# --- the final output (issue #101) ------------------------------------------------------


class _Slot(BaseModel):
    day: str
    hour: int


@dataclasses.dataclass
class _Decision:
    approved: bool


async def _terminal(monkeypatch: pytest.MonkeyPatch, final_output: Any, new_items: Sequence[Any] = ()) -> RunCompleted:
    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FakeStreamResult:
            return _FakeStreamResult(final_output, new_items)

    monkeypatch.setattr(executor_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsExecutor(ExecutionStore())
    payloads = [payload async for payload in engine.execute(_spec(), coerce_input("hi"), [], _ctx())]
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
        UnknownBlock(type="video", raw_block={"type": "video"}),
    ],
)
def test_unsupported_blocks_raise_naming_the_block_and_the_engine(block):
    with pytest.raises(ConfigError, match="openai-agents engine"):
        _to_sdk_input([TextBlock(text="hi"), block], use_responses=True)


def test_resource_block_raises_naming_the_uri_and_why_it_differs_from_data():
    """A ``ResourceBlock`` is a pointer, not content  -  the engine never fetches it, so it keeps
    refusing (issue #226) even though ``DataBlock`` on the same code path now renders."""
    with pytest.raises(ConfigError, match=r"s3://bucket/key.*never fetches it"):
        _to_sdk_input([TextBlock(text="hi"), ResourceBlock(uri="s3://bucket/key")], use_responses=True)


# --- DataBlock renders as JSON text (#226) ------------------------------------------------


def test_a_data_block_renders_as_its_own_json_text_part():
    """The canonical rendering: ``json.dumps(block.data)``, nothing wrapped around it  -  it used
    to raise ``ConfigError`` instead."""
    blocks = [TextBlock(text="what page am I on?"), DataBlock(data={"page": "reference/deck"})]
    assert _to_sdk_input(blocks, use_responses=True) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "what page am I on?"},
                {"type": "input_text", "text": '{"page": "reference/deck"}'},
            ],
        }
    ]


def test_a_data_only_input_still_takes_the_item_path_not_the_joined_str_path():
    """A single ``DataBlock`` is not text, so the all-text fast path (a joined ``str``) must not
    swallow it  -  it has to reach ``_part_of`` and come back as a list item."""
    result = _to_sdk_input([DataBlock(data={"a": 1})], use_responses=True)
    assert result == [{"role": "user", "content": [{"type": "input_text", "text": '{"a": 1}'}]}]


def test_a_data_block_value_that_looks_like_a_delimiter_stays_inside_the_json():
    """Why a bare ``json.dumps`` was chosen over a hand-rolled ``<context>...</context>``
    preamble: there is no paired open/close token here for embedded data to spoof. A value equal
    to a closing tag, or to a markdown code fence, lands inside the rendered JSON's own quotes  -
    escaped like any other string content  -  rather than breaking out of anything."""
    data = {"note": "</context>", "fence": "```", "quoted": 'a " and a \\ inside'}
    rendered = _to_sdk_input([DataBlock(data=data)], use_responses=True)[0]["content"][0]["text"]
    assert rendered == json.dumps(data, ensure_ascii=False)
    # The quote is the character that would end a string early if anything here concatenated
    # rather than serialised, so round-tripping it back is what proves the escaping held.
    assert json.loads(rendered) == data
    assert rendered.count("{") == rendered.count("}") == 1


def test_the_sdk_converter_is_the_oracle_for_the_emitted_shape():
    """``Converter.items_to_messages`` is the SDK's own chat-completions converter  -  feeding it
    what ``_to_sdk_input`` emits proves the shape agentdeck writes is the shape the SDK expects,
    without agentdeck writing a converter of its own."""
    blocks = [
        TextBlock(text="look"),
        ImageBlock(media_type="image/png", data_b64="AAAA"),
        AudioBlock(media_type="audio/ogg; codecs=opus", data_b64="BBBB"),
        DataBlock(data={"a": 1}),
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
                {"type": "text", "text": '{"a": 1}'},
            ],
        }
    ]


async def test_execute_hands_the_sdk_boundary_the_multimodal_item_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not just a unit test of ``_to_sdk_input`` in isolation  -  the exact value ``engine.start``
    passes ``Runner.run_streamed`` as its ``message`` argument."""
    captured: dict[str, Any] = {}

    class _FakeRunner:
        @staticmethod
        def run_streamed(*args: Any, **_kwargs: Any) -> _FakeStreamResult:
            captured["message"] = args[1]
            return _FakeStreamResult()

    monkeypatch.setattr(executor_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsExecutor(ExecutionStore())
    blocks = [TextBlock(text="what is this?"), ImageBlock(media_type="image/png", data_b64="AAAA")]
    async for _ in engine.execute(_spec(), blocks, [], _ctx()):
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


async def test_execute_raises_for_audio_under_responses_before_touching_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NeverCalledRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FakeStreamResult:
            raise AssertionError("must not reach the SDK boundary")

    monkeypatch.setattr(executor_module, "Runner", _NeverCalledRunner)
    engine = OpenAIAgentsExecutor(ExecutionStore())  # default settings: use_responses=True
    with pytest.raises(ConfigError, match="Responses API"):
        async for _ in engine.execute(_spec(), [AudioBlock(media_type="audio/wav", data_b64="AAAA")], [], _ctx()):
            pass


# --- #636: the artifacts a finished run produced, ahead of its final output -----------------

_ARTIFACT_AGENT = Agent(name="Artifacts", model=_NeverCalledModel())
"""Module level on purpose: ``RunItemBase`` keeps its agent by weak reference, so an item built
from a local one loses it before the assertion runs."""

_PNG_B64 = base64.b64encode(b"pretend png bytes").decode()
_OVER_CAP_B64 = base64.b64encode(b"\0" * (INLINE_BYTES_CAP + 1)).decode()


def _image_call(status: str, result: str | None) -> ToolCallItem:
    raw = ImageGenerationCall(id="ig-1", result=result, status=status, type="image_generation_call")
    return ToolCallItem(agent=_ARTIFACT_AGENT, raw_item=raw)


def _tool_output(output: Any) -> ToolCallOutputItem:
    raw = {"call_id": "call-1", "output": "sent", "type": "function_call_output"}
    return ToolCallOutputItem(agent=_ARTIFACT_AGENT, raw_item=raw, output=output)


def _message(text: str) -> MessageOutputItem:
    raw = ResponseOutputMessage(
        id="msg-1",
        content=[ResponseOutputText(text=text, type="output_text", annotations=[])],
        role="assistant",
        status="completed",
        type="message",
    )
    return MessageOutputItem(agent=_ARTIFACT_AGENT, raw_item=raw)


def test_a_completed_image_generation_call_becomes_an_image_block():
    assert run_artifacts([_image_call("completed", _PNG_B64)]) == [
        ImageBlock(media_type="image/png", data_b64=_PNG_B64)
    ]


@pytest.mark.parametrize("call", [_image_call("in_progress", None), _image_call("failed", None)])
def test_an_image_generation_call_without_a_result_produces_nothing(call):
    assert run_artifacts([call]) == []


def test_a_tool_image_data_url_becomes_an_inline_block_with_the_url_s_own_media_type():
    output = ToolOutputImage(image_url=f"data:image/webp;base64,{_PNG_B64}")
    assert run_artifacts([_tool_output(output)]) == [ImageBlock(media_type="image/webp", data_b64=_PNG_B64)]


def test_a_tool_image_http_url_stays_a_pointer():
    output = ToolOutputImage(image_url="https://example.test/chart.png")
    assert run_artifacts([_tool_output(output)]) == [ResourceBlock(uri="https://example.test/chart.png")]


def test_a_tool_file_url_becomes_a_resource_block():
    output = ToolOutputFileContent(file_url="https://example.test/report.pdf")
    assert run_artifacts([_tool_output(output)]) == [ResourceBlock(uri="https://example.test/report.pdf")]


@pytest.mark.parametrize(
    "output",
    [ToolOutputImage(file_id="file-1"), ToolOutputFileContent(file_id="file-1"), ToolOutputFileContent(file_data="AA")],
)
def test_a_tool_output_with_no_dereferenceable_uri_is_skipped(output):
    """A provider file id points at nothing this run's reader can fetch, and inline file bytes
    have no block to land in; both are dropped rather than guessed at."""
    assert run_artifacts([_tool_output(output)]) == []


def test_a_list_of_tool_outputs_contributes_each_of_its_media_in_order():
    output = [
        ToolOutputImage(image_url=f"data:image/png;base64,{_PNG_B64}"),
        ToolOutputText(text="ignored: text is not an artifact"),
        ToolOutputFileContent(file_url="https://example.test/report.pdf"),
    ]
    assert run_artifacts([_tool_output(output)]) == [
        ImageBlock(media_type="image/png", data_b64=_PNG_B64),
        ResourceBlock(uri="https://example.test/report.pdf"),
    ]


def test_an_artifact_over_the_inline_cap_is_dropped_and_the_rest_of_the_result_survives(caplog):
    """The run has already finished; raising here would fail it at its terminal event."""
    items = [_image_call("completed", _OVER_CAP_B64), _tool_output(ToolOutputImage(image_url="https://e.test/a.png"))]
    with caplog.at_level(logging.WARNING):
        assert run_artifacts(items) == [ResourceBlock(uri="https://e.test/a.png")]
    assert str(INLINE_BYTES_CAP + 1) in caplog.text
    assert str(INLINE_BYTES_CAP) in caplog.text


def test_an_unknown_item_type_leaves_an_otherwise_valid_run_alone():
    assert run_artifacts([_message("hello"), object()]) == []


async def test_a_plain_text_run_still_produces_exactly_its_final_text_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MessageOutputItem`` is deliberately unmapped: ``final_output`` already is that text."""
    terminal = await _terminal(monkeypatch, "ok", [_message("ok")])
    assert terminal.output == [TextBlock(text="ok")]


async def test_artifacts_lead_the_result_and_the_final_output_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel projecting this sends the image, then captions it."""
    items = [_image_call("completed", _PNG_B64), _message("Here is the image.")]
    terminal = await _terminal(monkeypatch, "Here is the image.", items)
    assert terminal.output == [
        ImageBlock(media_type="image/png", data_b64=_PNG_B64),
        TextBlock(text="Here is the image."),
    ]


async def test_a_structured_result_keeps_its_artifacts_and_its_data_block(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = await _terminal(monkeypatch, _Slot(day="tuesday", hour=9), [_image_call("completed", _PNG_B64)])
    assert terminal.output == [
        ImageBlock(media_type="image/png", data_b64=_PNG_B64),
        DataBlock(data={"day": "tuesday", "hour": 9}),
    ]
