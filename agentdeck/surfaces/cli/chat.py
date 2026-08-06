"""~50-line CLI chat renderer — the reference consumer for the event stream.

Distinguishes bubbles using **only** ``event.origin`` and ``payload.message_id`` — the
label is ``origin``, the bubble boundary is ``message_id``; grep this file for any other
mechanism and you won't find one. The transcript is rebuilt from
``message.completed`` alone: ``text.delta`` is read for nothing, so there is no delta
assembly to get wrong.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agentdeck.core.events import (
    ControlObserved,
    ControlRequested,
    MessageCompleted,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallCompleted,
    ToolCallStarted,
    parse_event,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx


async def render(lines: AsyncIterator[str]) -> None:
    """Print one line per bubble: a user turn, a tool notice, a completed message, a control
    notice, or the run's close. Everything else (deltas, ``custom``, ``node.updated``, ...)
    is skipped by design, per this module's docstring — the ``case _`` default below."""
    async for line in lines:
        if not line.startswith("data: "):
            continue  # blank keep-alives and any `event: ...` framing line
        event = parse_event(json.loads(line.removeprefix("data: ")))
        match event.payload:
            case RunStarted(input=blocks):
                text = " ".join(block.text for block in blocks if block.type == "text")
                print(f"You: {text}")
            case ToolCallStarted(tool=tool):
                print(f"[tool] {tool} called")
            case ToolCallCompleted(tool=tool, result_preview=preview):
                print(f"[tool] {tool} -> {preview}")
            case MessageCompleted(message_id=message_id, text=text):
                print(f"{event.origin} [{message_id}]: {text}")
            case ControlRequested(verb=verb, reason=reason):
                # Between a request and the run acting on it there can be a whole tool call,
                # so a reader watching a stream that has gone quiet is told which it is.
                print(f"[control] {verb} requested" + (f": {reason}" if reason else ""))
            case ControlObserved(verb=verb, safe_point=safe_point):
                print(f"[control] {verb} observed at {safe_point}")
            case RunCompleted() | RunFailed() | RunCancelled():
                print(f"-- {event.kind} --")
            case _:
                pass


async def stream_chat(client: httpx.AsyncClient, name: str, session_id: str, message: str) -> None:
    """Post one chat turn to the crude SSE route (``surfaces/serve/app.py``) and render it
    live — the thing this whole module exists to prove works end to end."""
    async with client.stream(
        "POST", f"/v2/invocables/{name}/chat", json={"session_id": session_id, "message": message}
    ) as response:
        await render(response.aiter_lines())


__all__ = ["render", "stream_chat"]
