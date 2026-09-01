"""Translation between AgentDeck's canonical events/content and the AG-UI wire, both
directions (``docs/design/protocols/agui.md``, ruling 48). No HTTP or SSE lifecycle here
(``binding.py`` owns that); no target-resolution or gateway calls either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ag_ui.core import (
    AudioInputContent,
    BaseEvent,
    CustomEvent,
    DocumentInputContent,
    ImageInputContent,
    Interrupt,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    TextInputContent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from agentdeck import AudioBlock, ImageBlock, ResourceBlock, TextBlock
from agentdeck.errors import InputError

if TYPE_CHECKING:
    from collections.abc import Callable

    from ag_ui.core import InputContentPart, RunAgentInput

    from agentdeck import ContentBlock, Event


@dataclass
class AdapterState:
    """Per AG-UI interaction: its own ``threadId``/``runId``, and which text or reasoning
    segment (if any) is currently open, so a later delta knows whether to start a new one."""

    thread_id: str
    run_id: str
    text_message_id: str | None = None
    reasoning_message_id: str | None = None


def to_agui_event(event: Event, state: AdapterState) -> list[BaseEvent]:
    """Zero, one or several AG-UI events for one canonical AgentDeck event, mutating ``state``
    as segments open and close. A kind this module has no projection for  -  unknown to this
    schema version, or known but not part of the table  -  is skipped, never breaks the stream.
    """
    projector = _PROJECTIONS.get(event.kind)
    return projector(event.payload, state) if projector is not None else []


def to_agentdeck_input(run_input: RunAgentInput) -> str | list[ContentBlock]:
    """The new user turn, never the whole transcript (ruling 45): only the last message, and
    only when it is a normal user turn.
    """
    if not run_input.messages:
        raise InputError("RunAgentInput.messages is empty: nothing to send as input")
    last = run_input.messages[-1]
    if last.role != "user":
        raise InputError(
            f"the last message must be a new user turn; got role {last.role!r}. Editing a "
            "transcript, regenerating, or appending a system/developer message is not "
            "supported yet (agui.md gap table)."
        )
    content = last.content
    if isinstance(content, str):
        return content
    blocks = [_content_block(part) for part in content]
    # A bare string round-trips through a single-parameter workflow (`_content_for`); a block
    # list does not, so a plain-text turn stays a string even when AG-UI sent it as one part.
    if len(blocks) == 1 and isinstance(blocks[0], TextBlock):
        return blocks[0].text
    return blocks


def to_agentdeck_resume(run_input: RunAgentInput) -> Any:
    """The answer for ``Run.answer()``: the one ``ResumeEntry``'s payload. ``binding.py``
    validates shape and matches the interrupt id before calling this.
    """
    assert run_input.resume, "to_agentdeck_resume is only called for a resume request"
    return run_input.resume[0].payload


def _content_block(part: InputContentPart) -> ContentBlock:
    match part:
        case TextInputContent(text=text):
            return TextBlock(text=text)
        case ImageInputContent(source=source):
            return _media_block(source, ImageBlock)
        case AudioInputContent(source=source):
            return _media_block(source, AudioBlock)
        case DocumentInputContent(source=source):
            if source.type == "url":
                return ResourceBlock(uri=source.value, media_type=source.mime_type)
            raise InputError("an inline document has no AgentDeck content block yet; send it by URL")
        case _:
            raise InputError(f"{type(part).__name__} has no AgentDeck content block yet")


def _media_block(source: Any, inline_cls: type[ImageBlock] | type[AudioBlock]) -> ContentBlock:
    if source.type == "data":
        return inline_cls(media_type=source.mime_type, data_b64=source.value)
    return ResourceBlock(uri=source.value, media_type=source.mime_type)


def _close_reasoning(state: AdapterState) -> list[BaseEvent]:
    if state.reasoning_message_id is None:
        return []
    message_id = state.reasoning_message_id
    state.reasoning_message_id = None
    return [ReasoningMessageEndEvent(message_id=message_id), ReasoningEndEvent(message_id=message_id)]


def _text_delta(payload: Any, state: AdapterState) -> list[BaseEvent]:
    events = _close_reasoning(state)
    if state.text_message_id != payload.message_id:
        if state.text_message_id is not None:
            events.append(TextMessageEndEvent(message_id=state.text_message_id))
        events.append(TextMessageStartEvent(message_id=payload.message_id))
        state.text_message_id = payload.message_id
    events.append(TextMessageContentEvent(message_id=payload.message_id, delta=payload.text))
    return events


def _message_completed(payload: Any, state: AdapterState) -> list[BaseEvent]:
    events = _close_reasoning(state)
    if state.text_message_id != payload.message_id:
        if state.text_message_id is not None:
            events.append(TextMessageEndEvent(message_id=state.text_message_id))
        events.append(TextMessageStartEvent(message_id=payload.message_id))
        events.append(TextMessageContentEvent(message_id=payload.message_id, delta=payload.text))
    events.append(TextMessageEndEvent(message_id=payload.message_id))
    state.text_message_id = None
    return events


def _thought_delta(payload: Any, state: AdapterState) -> list[BaseEvent]:
    events: list[BaseEvent] = []
    if state.reasoning_message_id != payload.message_id:
        events += _close_reasoning(state)
        events.append(ReasoningStartEvent(message_id=payload.message_id))
        events.append(ReasoningMessageStartEvent(message_id=payload.message_id, role="reasoning"))
        state.reasoning_message_id = payload.message_id
    events.append(ReasoningMessageContentEvent(message_id=payload.message_id, delta=payload.text))
    return events


def _tool_call_started(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        *_close_reasoning(state),
        ToolCallStartEvent(tool_call_id=payload.call_id, tool_call_name=payload.tool),
        ToolCallArgsEvent(tool_call_id=payload.call_id, delta=json.dumps(payload.args)),
        ToolCallEndEvent(tool_call_id=payload.call_id),
    ]


def _tool_call_completed(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        ToolCallResultEvent(message_id=payload.call_id, tool_call_id=payload.call_id, content=payload.result_preview)
    ]


def _run_completed(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        *_close_reasoning(state),
        RunFinishedEvent(thread_id=state.thread_id, run_id=state.run_id, outcome=RunFinishedSuccessOutcome()),
    ]


def _run_failed(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [*_close_reasoning(state), RunErrorEvent(message=payload.message, code=payload.error_code)]


def _run_cancelled(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [*_close_reasoning(state), RunErrorEvent(message="cancelled", code="cancelled")]


def _run_interrupted(payload: Any, state: AdapterState) -> list[BaseEvent]:
    question = payload.payload.get("question")
    options = payload.payload.get("options")
    interrupt = Interrupt(
        id=payload.interrupt_id,
        reason=payload.reason,
        message=str(question) if question is not None else None,
        response_schema={"enum": options} if options is not None else None,
    )
    return [
        *_close_reasoning(state),
        RunFinishedEvent(
            thread_id=state.thread_id, run_id=state.run_id, outcome=RunFinishedInterruptOutcome(interrupts=[interrupt])
        ),
    ]


def _run_paused(payload: Any, state: AdapterState) -> list[BaseEvent]:
    # No official outcome fits a pause without misreading it as a question, so it is data instead.
    return [
        *_close_reasoning(state),
        CustomEvent(name="agentdeck.paused", value={"reason": payload.reason}),
        RunFinishedEvent(thread_id=state.thread_id, run_id=state.run_id, outcome=RunFinishedSuccessOutcome()),
    ]


def _agent_changed(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        CustomEvent(
            name="agentdeck.agent_changed",
            value={"previous_agent": payload.previous_agent, "next_agent": payload.next_agent},
        )
    ]


def _artifact_created(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        CustomEvent(
            name="agentdeck.artifact",
            value={
                "artifact_id": payload.artifact_id,
                "media_type": payload.media_type,
                "uri": payload.uri,
                "size": payload.size,
            },
        )
    ]


def _usage_reported(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [CustomEvent(name="agentdeck.usage", value={"model": payload.model, "usage": payload.usage.model_dump()})]


def _reported(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return [
        CustomEvent(
            name="agentdeck.report",
            value={"level": payload.level, "message": payload.message, "fields": payload.fields},
        )
    ]


def _ignored(payload: Any, state: AdapterState) -> list[BaseEvent]:
    return []


_PROJECTIONS: dict[str, Callable[[Any, AdapterState], list[BaseEvent]]] = {
    "run.started": _ignored,  # the binding's own RUN_STARTED already opened the interaction (ruling 50)
    "run.completed": _run_completed,
    "run.failed": _run_failed,
    "run.cancelled": _run_cancelled,
    "run.interrupted": _run_interrupted,
    "run.paused": _run_paused,
    "run.resumed": _ignored,  # the next segment's own events carry it
    "text.delta": _text_delta,
    "message.completed": _message_completed,
    "thought.delta": _thought_delta,
    "tool.call.started": _tool_call_started,
    "tool.call.completed": _tool_call_completed,
    "agent.changed": _agent_changed,
    "artifact.created": _artifact_created,
    "usage.reported": _usage_reported,
    "report": _reported,
    "control.requested": _ignored,
    "control.observed": _ignored,
}

__all__ = ["AdapterState", "to_agentdeck_input", "to_agentdeck_resume", "to_agui_event"]
