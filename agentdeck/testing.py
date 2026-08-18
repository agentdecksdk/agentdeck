"""The test harness this repo (and any downstream deck) stubs a chat turn with.

The SDK boundary is the only thing ever stubbed here  -  everything above it (a resolved
``RunConfig``, the compat engine, the Runtime, a surface's frame rendering) stays the code
under test. Two shapes cover it:

- :class:`ScriptedModel` plus :func:`patch_model`  -  an in-process
  ``agents.models.interface.Model`` for a test that builds a ``Runtime``/``Deck`` directly.
- :func:`scripted_model_server`  -  a local Chat-Completions-compatible HTTP endpoint for a
  test that must go through a real subprocess or a real HTTP client, so an in-process model
  can't be reached (``OPENAI_BASE_URL`` points at it instead).
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

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
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence

_MODEL_NAME = "fake-scripted"
_CHAT_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

# Two places still resolve a run config: the Runtime plays a turn through the openai-agents
# adapter, while a workflow node driving an agent of its own still goes through the
# direct-call runner. Patching only one would pass a test while the other reached for a
# real endpoint.
_PROVIDER_TARGETS = (
    "agentdeck.authoring.runners.agent.OpenAIProvider",
    "agentdeck.adapters.engines.openai_agents.runconfig.OpenAIProvider",
)


def _usage(input_tokens: int, output_tokens: int) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=input_tokens + output_tokens,
    )


class ScriptedModel(Model):
    """Answers in ``deltas``; optionally calls ``tool_name`` once first, or raises mid-stream.

    ``inputs`` records what each call was handed, so a test can prove two turns shared one
    session without reaching into the session store. Every id, timestamp and default token
    count is a fixed constant, so a byte-level snapshot built on this model never varies.
    """

    def __init__(
        self,
        deltas: Sequence[str] = ("hi",),
        *,
        final_text: str | None = None,
        tool_name: str | None = None,
        raises: BaseException | None = None,
        hold: asyncio.Event | None = None,
        input_tokens: int = 3,
        output_tokens: int = 4,
    ) -> None:
        self.deltas = tuple(deltas)
        # The completed message, when it must differ from the joined deltas  -  which is how a
        # test tells "the SDK's final_output" apart from "the deltas, re-joined".
        self.final_text = final_text
        self.tool_name = tool_name
        self.raises = raises
        # Stall the turn after its first delta until `hold` is set, announcing it on `holding`.
        # A test that has to catch a consumer *inside* its next-event await needs the run to
        # stop where it says, not where a sleep happens to land.
        self.hold = hold
        self.holding = asyncio.Event()
        self.calls = 0
        self.inputs: list[Any] = []
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    def _response(self, output: list[Any]) -> Response:
        return Response(
            id=f"resp_scripted_{self.calls}",
            created_at=0.0,
            model=_MODEL_NAME,
            object="response",
            output=output,
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
            usage=_usage(self._input_tokens, self._output_tokens),
        )

    async def stream_response(self, _instructions: Any = None, input: Any = None, *_a: Any, **_k: Any) -> AsyncIterator:
        self.calls += 1
        self.inputs.append(input)
        if self.tool_name is not None and self.calls == 1:
            yield ResponseCompletedEvent(
                response=self._response(
                    [
                        ResponseFunctionToolCall(
                            id="fc_scripted_1",
                            call_id="call_scripted_1",
                            name=self.tool_name,
                            arguments="{}",
                            type="function_call",
                        )
                    ]
                ),
                sequence_number=0,
                type="response.completed",
            )
            return
        for index, delta in enumerate(self.deltas):
            if index and self.hold is not None:
                self.holding.set()
                await self.hold.wait()
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=delta,
                item_id="msg_scripted_1",
                logprobs=[],
                output_index=0,
                sequence_number=index,
                type="response.output_text.delta",
            )
        if self.raises is not None:
            raise self.raises
        text = self.final_text if self.final_text is not None else "".join(self.deltas)
        yield ResponseCompletedEvent(
            response=self._response(
                [
                    ResponseOutputMessage(
                        id="msg_scripted_1",
                        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ]
            ),
            sequence_number=len(self.deltas),
            type="response.completed",
        )

    def _sdk_usage(self) -> Usage:
        return Usage(
            requests=1,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._input_tokens + self._output_tokens,
        )

    async def get_response(self, _instructions: Any = None, input: Any = None, *_a: Any, **_k: Any) -> ModelResponse:
        self.calls += 1
        self.inputs.append(input)
        if self.tool_name is not None and self.calls == 1:
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="fc_scripted_1",
                        call_id="call_scripted_1",
                        name=self.tool_name,
                        arguments="{}",
                        type="function_call",
                    )
                ],
                usage=self._sdk_usage(),
                response_id="resp_scripted_1",
            )
        if self.raises is not None:
            raise self.raises
        text = self.final_text if self.final_text is not None else "".join(self.deltas)
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="msg_scripted_1",
                    content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ],
            usage=self._sdk_usage(),
            response_id="resp_scripted_1",
        )


def _provider_class(model: Model | Callable[[], Model]) -> type:
    """A drop-in ``OpenAIProvider``: a fixed ``model`` hands every lookup the same object,
    which is what lets a test read ``model.calls``/``model.inputs`` across turns. A zero-arg
    callable is invoked fresh each time a provider is constructed instead  -  the shape a
    per-turn reset (a fresh model, starting at turn one, for every ``RunConfig`` built) needs.
    """

    class _Provider:
        def __init__(self, **_kwargs: Any) -> None:
            self._model: Model = model if isinstance(model, Model) else model()

        def get_model(self, _name: str | None = None) -> Model:
            return self._model

    return _Provider


@contextmanager
def patch_model(model: Model | Callable[[], Model]) -> Iterator[None]:
    """Swap every place a run's model provider is built for a resolved ``model``, for the
    duration of the block.
    """
    provider = _provider_class(model)
    with patch(_PROVIDER_TARGETS[0], provider), patch(_PROVIDER_TARGETS[1], provider):
        yield


def _tool_call_delta(tool_name: str, tool_arguments: str, call_id: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": tool_arguments},
            }
        ],
    }


class _ScriptedChatHandler(BaseHTTPRequestHandler):
    """One scripted Chat-Completions turn per request: request *n* (0-indexed) calls
    ``tool_names[n]`` for as long as the list reaches that far, and every request past the end
    of it answers with ``reply``. Flat JSON or an SSE stream of chunks, whichever the request's
    own ``stream`` flag asks for  -  the minimum a real streaming reply is shaped as (a
    role-opening chunk, one content chunk, one finish chunk), so the SDK's stream parser
    produces a real delta and a real completed message instead of silently seeing nothing.
    """

    reply = "hi"
    tool_names: tuple[str, ...] = ()
    tool_arguments = "{}"
    received: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.received.append(body)
        call_index = len(self.received) - 1
        if call_index < len(self.tool_names):
            finish_reason, message = (
                "tool_calls",
                _tool_call_delta(
                    self.tool_names[call_index], self.tool_arguments, call_id=f"call_scripted_{call_index + 1}"
                ),
            )
        else:
            finish_reason, message = "stop", {"role": "assistant", "content": self.reply}
        if body.get("stream"):
            self._stream(finish_reason, message)
        else:
            self._complete(finish_reason, message)

    def _complete(self, finish_reason: str, message: dict[str, Any]) -> None:
        body = json.dumps(
            {
                "id": "chatcmpl-scripted",
                "object": "chat.completion",
                "created": 0,
                "model": _MODEL_NAME,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": _CHAT_USAGE,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, finish_reason: str, message: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if finish_reason == "tool_calls":
            chunks: list[dict[str, Any]] = [
                {"delta": message, "finish_reason": None},
                {"delta": {}, "finish_reason": "tool_calls"},
            ]
        else:
            chunks = [
                {"delta": {"role": "assistant", "content": ""}, "finish_reason": None},
                {"delta": {"content": message["content"]}, "finish_reason": None},
                {"delta": {}, "finish_reason": "stop", "usage": _CHAT_USAGE},
            ]
        for chunk in chunks:
            usage = chunk.pop("usage", None)
            payload = {
                "id": "chatcmpl-scripted",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": _MODEL_NAME,
                "choices": [{"index": 0, **chunk}],
            }
            if usage is not None:
                payload["usage"] = usage
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format: str, *args: Any) -> None:  # silence the default access log
        pass


@contextmanager
def scripted_model_server(
    reply: str = "hi",
    *,
    tool_name: str | Sequence[str] | None = None,
    tool_arguments: str = "{}",
    received: list[dict[str, Any]] | None = None,
) -> Iterator[str]:
    """A local Chat-Completions-compatible endpoint, for a test that points
    ``OPENAI_BASE_URL`` at a real HTTP server rather than patching an in-process model  -
    running agentdeck as a real subprocess, or exercising a route that builds its own
    ``RunConfig``, neither of which :func:`patch_model` can reach.

    ``tool_name`` a single name: the first request gets that tool call, every one after answers
    in text. A sequence: request *n* gets ``tool_name[n]``'s call for as long as the sequence
    reaches that far (one call per turn  -  the shape a multi-step tool chain or a handoff
    round-trip needs), then every request past the end answers in text. Left ``None``: every
    request answers in text from the start. Pass a list as ``received`` to capture every
    request body handed to the endpoint, in order.
    """
    tool_names = () if tool_name is None else (tool_name,) if isinstance(tool_name, str) else tuple(tool_name)
    handler = type(
        "_Handler",
        (_ScriptedChatHandler,),
        {
            "reply": reply,
            "tool_names": tool_names,
            "tool_arguments": tool_arguments,
            "received": received if received is not None else [],
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join()


__all__ = ["ScriptedModel", "patch_model", "scripted_model_server"]
