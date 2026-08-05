"""UC1 — the handoff chat: FrontDesk hands off to ClaimsAgent, which calls a tool and
answers; turn 2 reuses turn-1 context; the transcript reads back from the store alone.
One asserted behavior is a known gap, not a feature: the Runtime's ``origin`` is
invocable-scoped, not sub-agent-scoped, so ClaimsAgent's answer prints under the
"FrontDesk" label too. That is a recorded design finding awaiting a ruling, not fixed
here; the bubble assertion below documents it on purpose.

Scripted fakes only (no network, no API keys): a fresh ``FrontModel``/``ClaimsModel`` pair
per test, so a call-count counter is safe — this file never shares an engine across tests
the way the generic contract-suite cases must (see ``openai_agents_cases.py``'s docstring).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from agents import Agent, function_tool
from agents.handoffs import Handoff
from agents.models.interface import Model
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

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.context import RunContext
from agentdeck.core.events import RESULT_PREVIEW_MAX, check_contiguous, parse_event
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.service import Runtime
from agentdeck.surfaces.cli.chat import stream_chat
from agentdeck.surfaces.serve.app import build_app

pytest.importorskip("fastapi")

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
SESSION_ID = "s1"
# Longer than RESULT_PREVIEW_MAX (4096) on purpose — the log truncates it, the SDK
# session must not: the session holds full bytes so the model never loses context.
LONG_RESULT = "Shipment 4412 was received damaged. " * 150
assert len(LONG_RESULT) > RESULT_PREVIEW_MAX


def _usage() -> ResponseUsage:
    return ResponseUsage(
        input_tokens=10,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=5,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=15,
    )


def _response(output: list[Any]) -> Response:
    return Response(
        id="resp_uc1",
        created_at=0.0,
        model="fake-uc1",
        object="response",
        output=output,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=_usage(),
    )


class FrontModel(Model):
    """Always speaks one sentence, then hands off — turn 2 gets the same shape rather
    than special-casing it."""

    def __init__(self, handoff_tool: str) -> None:
        self._handoff_tool = handoff_tool
        self.calls = 0

    async def stream_response(self, _system_instructions: str | None, _input: Any, *_a: Any, **_k: Any):
        self.calls += 1
        message_id = f"msg_front_{self.calls}"
        text = "Connecting you to a claims specialist."
        for chunk in (text[:12], text[12:]):
            yield ResponseTextDeltaEvent(
                content_index=0,
                delta=chunk,
                item_id=message_id,
                logprobs=[],
                output_index=0,
                sequence_number=0,
                type="response.output_text.delta",
            )
        output = [
            ResponseOutputMessage(
                id=message_id,
                content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                role="assistant",
                status="completed",
                type="message",
            ),
            ResponseFunctionToolCall(
                id=f"fc_handoff_{self.calls}",
                call_id=f"call_handoff_{self.calls}",
                name=self._handoff_tool,
                arguments="{}",
                type="function_call",
            ),
        ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("UC1's fixtures only stream")


class ClaimsModel(Model):
    """Turn 1: calls the tool, then answers from its result. Turn 2: answers again, first
    asserting the exact turn-1 tool result (untruncated) is still in its input — proof
    that execution state, not the log, fed the model."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_response(self, _system_instructions: str | None, input: Any, *_a: Any, **_k: Any):
        self.calls += 1
        if self.calls == 1:
            output: list[Any] = [
                ResponseFunctionToolCall(
                    id="fc_tool_1",
                    call_id="call_tool_1",
                    name="lookup_shipment",
                    arguments='{"shipment_id": "4412"}',
                    type="function_call",
                )
            ]
        else:
            if self.calls == 3:
                _assert_turn1_tool_result_present(input)
            message_id = f"msg_claims_{self.calls}"
            text = "It was damaged; a refund is pending." if self.calls == 2 else "5 to 7 business days."
            for chunk in (text[: len(text) // 2], text[len(text) // 2 :]):
                yield ResponseTextDeltaEvent(
                    content_index=0,
                    delta=chunk,
                    item_id=message_id,
                    logprobs=[],
                    output_index=0,
                    sequence_number=0,
                    type="response.output_text.delta",
                )
            output = [
                ResponseOutputMessage(
                    id=message_id,
                    content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
        yield ResponseCompletedEvent(response=_response(output), sequence_number=0, type="response.completed")

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("UC1's fixtures only stream")


def _assert_turn1_tool_result_present(input: list[Any]) -> None:
    """Turn 2's model input must contain turn 1's exact tool-result item, untruncated —
    proving the SDK session, not the truncated log, fed the model (ADR-D5's whole point)."""
    outputs = [
        item.get("output") for item in input if isinstance(item, dict) and item.get("type") == "function_call_output"
    ]
    assert LONG_RESULT in outputs, "turn 2 input is missing turn 1's untruncated tool result"


@function_tool
def lookup_shipment(shipment_id: str) -> str:
    """Look up a shipment's status."""
    return LONG_RESULT


def _build() -> tuple[Runtime, ExecutionStore, SqliteEventStore]:
    claims_agent = Agent(name="ClaimsAgent", instructions="handle claims", tools=[lookup_shipment], model=ClaimsModel())
    handoff_tool = Handoff.default_tool_name(claims_agent)
    front_agent = Agent(
        name="FrontDesk", instructions="route to claims", handoffs=[claims_agent], model=FrontModel(handoff_tool)
    )
    spec = InvocableSpec(
        name="FrontDesk", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=front_agent
    )
    sessions = ExecutionStore()
    store = SqliteEventStore()
    runtime = Runtime([OpenAIAgentsEngine(sessions)], store, {"FrontDesk": spec}, clock=lambda: TS)
    return runtime, sessions, store


def _read_ctx() -> RunContext:
    # The SSE surface fixes tenant/principal for every request (M0 fakes auth away);
    # reading the store back afterwards has to use the same identity.
    return RunContext(tenant="demo", principal="user:demo", run_id="n/a", trace_id="t", session_id=SESSION_ID)


async def test_uc1_handoff_chat_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    runtime, sessions, store = _build()
    app = build_app(runtime)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await stream_chat(client, "FrontDesk", SESSION_ID, "my shipment 4412 is damaged")
        await stream_chat(client, "FrontDesk", SESSION_ID, "and when will the refund arrive?")

    out = capsys.readouterr().out.splitlines()

    # --- "two labeled bubbles, never one smeared paragraph" -------------------------
    # message_id still gives each bubble a distinct boundary. origin does not additionally
    # say *who* spoke: the Runtime stamps origin = spec.name (the top-level invocable) for
    # every event of a run, so ClaimsAgent's answer prints under the "FrontDesk" label too
    # — a recorded design finding awaiting a ruling, not fixed by this adapter. This
    # assertion documents that gap on purpose, so it fails loudly (not silently) the day
    # speaker attribution changes.
    message_lines = [line for line in out if ": " in line and line.split(" [", 1)[0] in ("FrontDesk", "ClaimsAgent")]
    assert len(message_lines) == 4  # front's sentence + claims' answer, both turns
    assert len({line.split("]")[0] for line in message_lines}) == 4  # 4 distinct message_ids
    assert all(line.startswith("FrontDesk [") for line in message_lines)  # ClaimsAgent's turn never appears
    assert any("It was damaged" in line for line in message_lines)
    assert any("5 to 7 business days" in line for line in message_lines)

    # --- step 3: read the transcript back from the store only, no live stream --------
    ctx = _read_ctx()
    log = await store.read(SESSION_ID, ctx)
    completed_texts = [event.payload.text for event in log if event.kind == "message.completed"]
    assert completed_texts == [
        "Connecting you to a claims specialist.",
        "It was damaged; a refund is pending.",
        "Connecting you to a claims specialist.",
        "5 to 7 business days.",
    ]

    # --- seq contiguous from 0, per run --------------------------------------------
    for run_id in {event.run_id for event in log}:
        run_events = [event for event in log if event.run_id == run_id]
        assert check_contiguous(run_events) == []
        assert run_events[0].seq == 0

    # --- every emitted event validates round-trip ------------------------------------
    for event in log:
        assert parse_event(json.loads(event.model_dump_json())) == event

    # --- tool result: preview + hash + size in the log, full bytes in the SDK session --
    [tool_completed] = [event for event in log if event.kind == "tool.call.completed"]
    payload = tool_completed.payload
    assert payload.result_preview == LONG_RESULT[:RESULT_PREVIEW_MAX]
    assert len(payload.result_preview) < len(LONG_RESULT)
    assert payload.result_size == len(LONG_RESULT.encode())
    assert payload.result_sha256 == hashlib.sha256(LONG_RESULT.encode()).hexdigest()

    session = sessions.session_for(ctx)
    sdk_items = await session.get_items()
    sdk_tool_outputs = [
        item["output"]
        for item in sdk_items
        if isinstance(item, dict) and item.get("type") == "function_call_output" and item.get("output") == LONG_RESULT
    ]
    assert sdk_tool_outputs == [LONG_RESULT]  # untruncated, full bytes

    # --- transcript fidelity (ADR-D5): SDK-session transcript == event-log transcript,
    # content and order, no byte-level normalization ---------------------------------
    log_transcript = _message_transcript_from_log(log)
    sdk_transcript = _message_transcript_from_session(sdk_items)
    assert sdk_transcript == log_transcript


def _message_transcript_from_log(events: list[Any]) -> list[tuple[str, str]]:
    transcript: list[tuple[str, str]] = []
    for event in events:
        if event.kind == "run.started":
            text = " ".join(block.text for block in event.payload.input if block.type == "text")
            transcript.append(("user", text))
        elif event.kind == "message.completed":
            transcript.append(("assistant", event.payload.text))
    return transcript


def _message_transcript_from_session(items: list[Any]) -> list[tuple[str, str]]:
    transcript: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            continue
        content = item["content"]
        text = content if isinstance(content, str) else "".join(part.get("text", "") for part in content)
        transcript.append((item["role"], text))
    return transcript
