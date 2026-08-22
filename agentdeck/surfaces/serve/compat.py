"""v1's chat wire format, rendered from canonical events.

The v1 endpoints predate the event schema: their frames carry ``delta`` / ``done`` / ``error``
names and an aggregate ``usage`` dict that no event kind describes. Translating them here rather
than in core is deliberate (D10: a consumer shapes what it needs, the schema does not grow a
surface's frame shapes)  -  this module is the only place that knows what v1 puts on the wire,
and it reads nothing but ``Event`` objects to do it.

v1 has no isolation boundary of its own, so every run through this facade is unnamespaced;
a caller that needs runs kept apart passes a namespace per run.
"""

from __future__ import annotations

import json
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import DataBlock, TextBlock
from agentdeck.core.events import RunCompleted, TextDelta, UsageReported

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from agentdeck.core.events import Event


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
        elif isinstance(payload, RunCompleted):
            if self.output is None:
                # An `output_type` agent's validated result rides `RunCompleted.output` as a
                # `DataBlock`; anything else joins as text.
                data = next((block.data for block in payload.output if isinstance(block, DataBlock)), None)
                self.output = (
                    data
                    if data is not None
                    else "".join(block.text for block in payload.output if isinstance(block, TextBlock))
                )
            self.usage = {
                "requests": self.requests,
                "input_tokens": payload.usage.input_tokens,
                "output_tokens": payload.usage.output_tokens,
                "total_tokens": payload.usage.input_tokens + payload.usage.output_tokens,
            }


async def chat_frames(events: AsyncGenerator[Event, None]) -> AsyncIterator[str]:
    """v1's chat SSE: one ``delta`` frame per text delta, then one ``done`` frame.

    A mid-stream failure ends the run with ``error`` instead of ``done``, carrying the
    exception's type name and never its message  -  the same in-band report v1 makes once
    the status code is already on the wire.

    ``aclosing`` covers only one of the two shapes a disconnect takes, and not the common one:
    a server that leaves this generator suspended, whose next reader closes it. What the shipped
    stack actually does is **cancel** the task streaming the response, and that ``CancelledError``
    travels into whatever ``events`` is  -  fed a live ``runtime.run()`` generator directly (as a
    lower-level caller may), that closes the run there and the Runtime records ``run.cancelled``.
    Fed ``deck.stream()`` instead  -  what the shipped server actually calls  -  the run is a
    deck-owned task the disconnect never reaches (docs/design/run-identity.md §9): it keeps
    executing, and this generator only stops watching it. Neither path is this generator's to
    guarantee, so do not read this as one.
    """
    turn = _Turn()
    try:
        async with aclosing(events):
            async for event in events:
                turn.observe(event)
                if isinstance(event.payload, TextDelta):
                    yield f"data: {json.dumps({'delta': event.payload.text})}\n\n"
                elif isinstance(event.payload, RunCompleted):
                    done = {"output": turn.output, "usage": turn.usage}
                    yield f"event: done\ndata: {json.dumps(done, default=str)}\n\n"
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"


async def chat_result(events: AsyncGenerator[Event, None]) -> dict[str, Any]:
    """v1's non-streamed chat body: the run's output, once it has one.

    The engine's exception reaches the caller unchanged, so a failed turn is still the
    500 (or the 404/422) v1 answered with, decided by the exception's own type. Closed the same
    way as the streamed path, with the same division of labour over a disconnect.
    """
    turn = _Turn()
    async with aclosing(events):
        async for event in events:
            turn.observe(event)
    return {"output": turn.output}


__all__ = ["chat_frames", "chat_result"]
