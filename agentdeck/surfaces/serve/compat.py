"""v1's chat and workflow wire formats, rendered from canonical events.

The v1 endpoints predate the event schema: their frames carry ``delta`` / ``done`` /
``error`` / ``node_update`` / ``custom`` / ``interrupt`` names, an aggregate ``usage`` dict
and a bare graph state that no event kind describes. Translating them here rather than in
core is deliberate (D10: a consumer shapes what it needs, the schema does not grow a
surface's frame shapes) — this module is the only place that knows what v1 puts on the wire,
and it reads nothing but ``Event`` objects to do it.

v1 has no isolation boundary of its own, so every run through this facade is unnamespaced;
a caller that needs runs kept apart passes a namespace per run. A workflow's
``thread_id`` is its session: v1's caller names the thread, resumes it later, and expects one
turn on it at a time — which is exactly what a session id buys from the Runtime.
"""

from __future__ import annotations

import json
from contextlib import aclosing
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import DataBlock, TextBlock
from agentdeck.core.events import Custom, NodeUpdated, RunCompleted, RunInterrupted, TextDelta, UsageReported

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from agentdeck.core.events import Event
    from agentdeck.runtime.service import PendingRun


# The langgraph engine's namespaced carrier for a ``get_stream_writer()`` write, which is
# what v1's ``custom`` frame carries. Spelled out for the same reason, pinned by the same
# kind of test.
STREAM_WRITE = "langgraph.stream_write"
STREAM_WRITE_KEY = "value"


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
    exception's type name and never its message — the same in-band report v1 makes once
    the status code is already on the wire.

    ``aclosing`` covers only one of the two shapes a disconnect takes, and not the common one:
    a server that leaves this generator suspended, whose next reader closes it. What the shipped
    stack actually does is **cancel** the task streaming the response, and that ``CancelledError``
    travels into whatever ``events`` is — fed a live ``runtime.run()`` generator directly (as a
    lower-level caller may), that closes the run there and the Runtime records ``run.cancelled``.
    Fed ``deck.stream()`` instead — what the shipped server actually calls — the run is a
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


async def workflow_frames(events: AsyncGenerator[Event, None]) -> AsyncIterator[str]:
    """v1's workflow SSE: one ``node_update`` frame per node update and one ``custom`` frame
    per stream write, then one ``done`` frame carrying the final state — or one ``interrupt``
    frame in its place when the graph paused for a human.

    A mid-run failure ends the stream with ``error`` and the exception's type name, never its
    message, exactly as the chat stream does and for the same reason: the status code is
    already on the wire. The division of labour over a disconnect is ``chat_frames``'s.
    """
    try:
        async with aclosing(events):
            async for event in events:
                frame = _workflow_frame(event.payload)
                if frame is not None:
                    yield frame
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"


async def workflow_result(events: AsyncGenerator[Event, None]) -> Any:
    """v1's non-streamed workflow body: the final state, or the interrupt the run paused on.

    ``None`` means the run produced neither, which is the empty stream of a claim somebody
    else won.
    """
    result, _ = await _terminal(events)
    return result


def interrupt_inbox(pending: Sequence[PendingRun], invocable: str) -> list[dict[str, Any]]:
    """v1's approval inbox for one workflow: every thread of it currently waiting on a human.

    Sorted by thread id, which is the order v1's own listing came back in — it walked the
    checkpointer's threads sorted, and a client that renders an inbox should not see it
    reshuffle between polls.
    """
    return [_pending(run) for run in sorted(pending, key=lambda run: run.thread_id) if run.invocable == invocable]


async def _terminal(events: AsyncGenerator[Event, None]) -> tuple[Any, bool]:
    """v1's terminal body for this run, plus whether the graph ran anything at all for it."""
    result: Any = None
    applied = False
    async with aclosing(events):
        async for event in events:
            payload = event.payload
            if isinstance(payload, RunInterrupted):
                result, applied = _interrupt(payload), True
            elif isinstance(payload, NodeUpdated):
                applied = True
            elif isinstance(payload, RunCompleted):
                result = _final_state(payload)
    return result, applied


def _workflow_frame(payload: Any) -> str | None:
    """One v1 frame for this payload, or ``None`` for an event v1's wire has no frame for
    (``run.started``, ``run.resumed``, and the ``run.failed`` whose report is the ``error``
    frame the exception itself produces)."""
    if isinstance(payload, NodeUpdated):
        # v1's wire showed ``"delta": null`` for a node that changed nothing, and
        # ``state_patch`` is a dict that cannot carry null — so an empty patch renders back as
        # null. Nothing else is flattened by that: langgraph reports a node returning ``{}``
        # and one returning ``None`` identically, and v1 showed null for both.
        return _data({"type": "node_update", "node": payload.node, "delta": payload.state_patch or None})
    if isinstance(payload, Custom) and payload.name == STREAM_WRITE:
        return _data({"type": "custom", "data": payload.data.get(STREAM_WRITE_KEY)})
    if isinstance(payload, RunInterrupted):
        return f"event: interrupt\ndata: {json.dumps(_interrupt(payload), default=str)}\n\n"
    if isinstance(payload, RunCompleted):
        return f"event: done\ndata: {json.dumps(_final_state(payload), default=str)}\n\n"
    return None


def _data(frame: dict[str, Any]) -> str:
    return f"data: {json.dumps(frame, default=str)}\n\n"


def _interrupt(payload: RunInterrupted) -> dict[str, Any]:
    """v1's paused-run shape, which is a thread id plus the question asked on it."""
    return {"type": "interrupt", "payload": payload.payload, "thread_id": payload.thread_id or ""}


def _pending(run: PendingRun) -> dict[str, Any]:
    return {"type": "interrupt", "payload": run.payload, "thread_id": run.thread_id}


def _final_state(payload: RunCompleted) -> Any:
    """A workflow's result is its graph state, which travels as one data block."""
    return next((block.data for block in payload.output if isinstance(block, DataBlock)), None)


__all__ = [
    "STREAM_WRITE",
    "STREAM_WRITE_KEY",
    "chat_frames",
    "chat_result",
    "interrupt_inbox",
    "workflow_frames",
    "workflow_result",
]
