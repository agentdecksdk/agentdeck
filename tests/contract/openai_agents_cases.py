"""The openai-agents adapter's cases for the shared contract suite.

Appended onto ``contract_cases.CASES`` — same invariants, a real engine instead of the
stub. ``TailScriptedModel`` decides purely from the *last* input item rather than an
internal call counter, which is what makes it safe to share one engine (and its cached
SDK session) across every test function in ``test_event_stream.py``: whatever stale
history a previous test left behind, a fresh run's tail is always its own new user
message, never a leftover tool result.
"""

from __future__ import annotations

from typing import Any

from agents import Agent, function_tool
from agents.models.interface import Model
from case_types import Case
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.core.invocable import InvocableKind, InvocableSpec

_USAGE = ResponseUsage(
    input_tokens=3,
    input_tokens_details=InputTokensDetails(cached_tokens=0),
    output_tokens=2,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    total_tokens=5,
)


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp_contract",
        created_at=0.0,
        model="fake-contract",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=_USAGE,
    )


def _is_tool_result(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") == "function_call_output"


class TailScriptedModel(Model):
    """No tool configured: always answers. A tool configured: calls it once, then answers
    once the tail shows its result — regardless of any stale history before that tail."""

    def __init__(self, answer: str, tool_name: str | None = None) -> None:
        self._answer = answer
        self._tool_name = tool_name

    async def stream_response(self, _system_instructions: str | None, input: Any, *_args: Any, **_kwargs: Any):
        last = input[-1] if input else None
        if self._tool_name is not None and not _is_tool_result(last):
            output: list[Any] = [
                ResponseFunctionToolCall(
                    id="fc_contract_1",
                    call_id="call_contract_1",
                    name=self._tool_name,
                    arguments="{}",
                    type="function_call",
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="msg_contract_1",
                    content=[ResponseOutputText(annotations=[], text=self._answer, type="output_text")],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("contract cases only stream")


class RaisingModel(Model):
    """Always raises — the engine's counterpart to the stub's ``raises-midstream`` case."""

    async def stream_response(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("engine blew up")
        yield  # pragma: no cover — makes this an async generator, never reached

    async def get_response(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("contract cases only stream")


@function_tool
def lookup_shipment(shipment_id: str) -> str:
    """Look up a shipment's status."""
    return "damaged"


def _spec(name: str, agent: Agent[Any]) -> InvocableSpec:
    return InvocableSpec(name=name, kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def openai_agents_cases() -> list[Case]:
    return [
        Case(
            id="openai-agents/completes",
            engine=OpenAIAgentsEngine(),
            spec=_spec("Chatty", Agent(name="Chatty", instructions="reply", model=TailScriptedModel("hello there"))),
            ends="terminal",
        ),
        Case(
            id="openai-agents/calls-a-tool",
            engine=OpenAIAgentsEngine(),
            spec=_spec(
                "Looker",
                Agent(
                    name="Looker",
                    instructions="use the tool",
                    tools=[lookup_shipment],
                    model=TailScriptedModel("it was damaged", tool_name="lookup_shipment"),
                ),
            ),
            ends="terminal",
        ),
        Case(
            id="openai-agents/raises-midstream",
            engine=OpenAIAgentsEngine(),
            spec=_spec("Boom", Agent(name="Boom", instructions="boom", model=RaisingModel())),
            ends="terminal",
        ),
    ]


__all__ = ["openai_agents_cases"]
