"""A scripted ``agents.models.interface.Model`` — the SDK boundary the goldens stub.

Two turns per run, identical for streamed and non-streamed calls: the first asks for
the fixture agent's ``lookup_slot`` tool, the second answers in text deltas. Every id,
token count and timestamp is a constant, so nothing variable can reach the wire.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ANSWER_DELTAS = ("Tuesday ", "at 9am ", "works.")
TOOL_NAME = "lookup_slot"

_USAGE = ResponseUsage(
    input_tokens=11,
    input_tokens_details=InputTokensDetails(cached_tokens=0),
    output_tokens=5,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    total_tokens=16,
)


def _response(output: list[Any], index: int) -> Response:
    return Response(
        id=f"resp_golden_{index}",
        created_at=0.0,
        model="fake-golden",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=_USAGE,
    )


def _tool_call_output() -> list[Any]:
    return [
        ResponseFunctionToolCall(
            id="fc_golden_1",
            call_id="call_golden_1",
            name=TOOL_NAME,
            arguments='{"day": "tuesday"}',
            type="function_call",
        )
    ]


def _message_output() -> list[Any]:
    return [
        ResponseOutputMessage(
            id="msg_golden_1",
            content=[ResponseOutputText(annotations=[], text="".join(ANSWER_DELTAS), type="output_text")],
            role="assistant",
            status="completed",
            type="message",
        )
    ]


class ScriptedModel(Model):
    """Turn 1 calls the tool, turn 2 (and any later turn) answers in text."""

    def __init__(self) -> None:
        self.turns = 0

    def _next_output(self) -> tuple[list[Any], int]:
        self.turns += 1
        if self.turns == 1:
            return _tool_call_output(), 1
        return _message_output(), self.turns

    async def get_response(self, *_args: Any, **_kwargs: Any) -> ModelResponse:
        output, index = self._next_output()
        return ModelResponse(
            output=output,
            usage=Usage(requests=1, input_tokens=11, output_tokens=5, total_tokens=16),
            response_id=f"resp_golden_{index}",
        )

    async def stream_response(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        output, index = self._next_output()
        sequence = 0
        if index > 1:
            for delta in ANSWER_DELTAS:
                yield ResponseTextDeltaEvent(
                    content_index=0,
                    delta=delta,
                    item_id="msg_golden_1",
                    logprobs=[],
                    output_index=0,
                    sequence_number=sequence,
                    type="response.output_text.delta",
                )
                sequence += 1
        yield ResponseCompletedEvent(
            response=_response(output, index),
            sequence_number=sequence,
            type="response.completed",
        )


class ScriptedProvider:
    """Drop-in for ``OpenAIProvider``: hands every lookup the same scripted model."""

    def __init__(self, **_kwargs: Any) -> None:
        self._model = ScriptedModel()

    def get_model(self, _name: str | None = None) -> Model:
        return self._model


__all__ = ["ANSWER_DELTAS", "TOOL_NAME", "ScriptedModel", "ScriptedProvider"]
