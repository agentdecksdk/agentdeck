"""v1's chat wire format, rendered from canonical events.

The v1 endpoints predate the event schema: their frames carry ``delta`` / ``done`` /
``error`` names and an aggregate ``usage`` dict that no event kind describes. Translating
them here rather than in core is deliberate (D10: a consumer shapes what it needs, the
schema does not grow a surface's frame shapes) — this module is the only place that knows
what v1 puts on the wire, and it reads nothing but ``Event`` objects to do it.

v1 has no tenancy and no auth, so every run through this facade shares one tenant and
principal; the ``/v2`` routes are where a real principal will arrive from.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import Custom, RunCompleted, TextDelta, UsageReported

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentdeck.core.events import Event

V1_TENANT = "local"
V1_PRINCIPAL = "user:local"

# The engine's namespaced carrier for an ``output_type`` result, which ``RunCompleted``
# can only hold as text. Spelled out rather than imported: a surface that imported an
# adapter would invert the direction the wiring depends on. A test pins it to the engine's
# own constant so the two cannot drift.
STRUCTURED_OUTPUT = "openai_agents.structured_output"


def run_context(session_id: str | None = None) -> RunContext:
    """A fresh context for one v1 request."""
    return RunContext(
        tenant=V1_TENANT,
        principal=V1_PRINCIPAL,
        run_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        session_id=session_id,
    )


class _Turn:
    """What v1's terminal frame needs, gathered as the run streams."""

    def __init__(self) -> None:
        self.output: Any = None
        self.usage: dict[str, int] = {}
        self.requests = 0

    def observe(self, event: Event) -> None:
        payload = event.payload
        if isinstance(payload, UsageReported):
            # v1 reports the SDK's cumulative Usage, whose `requests` counts model calls.
            self.requests += 1
        elif isinstance(payload, Custom) and payload.name == STRUCTURED_OUTPUT:
            self.output = payload.data.get("output")
        elif isinstance(payload, RunCompleted):
            if self.output is None:
                self.output = "".join(block.text for block in payload.output if isinstance(block, TextBlock))
            self.usage = {
                "requests": self.requests,
                "input_tokens": payload.usage.input_tokens,
                "output_tokens": payload.usage.output_tokens,
                "total_tokens": payload.usage.input_tokens + payload.usage.output_tokens,
            }


async def chat_frames(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    """v1's chat SSE: one ``delta`` frame per text delta, then one ``done`` frame.

    A mid-stream failure ends the run with ``error`` instead of ``done``, carrying the
    exception's type name and never its message — the same in-band report v1 makes once
    the status code is already on the wire.
    """
    turn = _Turn()
    try:
        async for event in events:
            turn.observe(event)
            if isinstance(event.payload, TextDelta):
                yield f"data: {json.dumps({'delta': event.payload.text})}\n\n"
            elif isinstance(event.payload, RunCompleted):
                done = {"output": turn.output, "usage": turn.usage}
                yield f"event: done\ndata: {json.dumps(done, default=str)}\n\n"
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"


async def chat_result(events: AsyncIterator[Event]) -> dict[str, Any]:
    """v1's non-streamed chat body: the run's output, once it has one.

    The engine's exception reaches the caller unchanged, so a failed turn is still the
    500 (or the 404/422) v1 answered with, decided by the exception's own type.
    """
    turn = _Turn()
    async for event in events:
        turn.observe(event)
    return {"output": turn.output}


__all__ = ["STRUCTURED_OUTPUT", "V1_PRINCIPAL", "V1_TENANT", "chat_frames", "chat_result", "run_context"]
