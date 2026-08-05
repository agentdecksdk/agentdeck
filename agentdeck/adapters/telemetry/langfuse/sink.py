"""``EventSinkPort`` that renders the canonical event stream as Langfuse traces.

One run is one trace: ``run.started`` opens the root observation, a terminal event closes
it, and what happens in between becomes children — tool calls as spans, reported model
usage as generations, workflow node updates as points on the timeline. Nothing here asks
whether the run was an agent or a workflow, which is exactly why both are traced by the
same code: the stream is all there is to read.

Two properties of the sink contract shape the whole module. ``emit`` must return promptly,
so every call does in-memory work only — the Langfuse SDK buffers observations and ships
them from its own background thread, and nothing here awaits a round trip. And the tap is
lossy: any event can be missing, so an observation left open by a lost ``completed`` event
is closed by the next one that implies it rather than waited for, and a run whose terminal
event never arrives is eventually evicted instead of leaking its spans forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import ImageBlock, ResourceBlock, TextBlock
from agentdeck.core.events import (
    NodeUpdated,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    RunPaused,
    RunResumed,
    RunStarted,
    ToolCallCompleted,
    ToolCallStarted,
    UsageReported,
)
from agentdeck.core.ports import EventSinkPort

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentdeck.adapters.telemetry.langfuse.trace import Level, Observation, ObservationKind, Tracer
    from agentdeck.core.content import Input
    from agentdeck.core.events import Event, Usage

logger = logging.getLogger(__name__)

# How many runs may be mid-trace at once. A run whose terminal event the tap dropped would
# otherwise hold its observations for the life of the process; the oldest is closed as
# abandoned to make room, which reports the loss instead of accumulating it.
MAX_OPEN_RUNS = 512

# What an invocable's kind means to Langfuse. A skill is a tool call by another name.
_OBSERVATION_OF: dict[str, ObservationKind] = {"agent": "agent", "workflow": "chain", "skill": "tool"}


@dataclass(slots=True)
class _OpenTrace:
    """A run's root observation and the tool calls still open under it."""

    root: Observation
    calls: dict[str, Observation] = field(default_factory=dict)


class LangfuseSink(EventSinkPort):
    """Renders each run as a Langfuse trace. Registered only when Langfuse is configured."""

    def __init__(self, tracer: Tracer, *, max_open_runs: int = MAX_OPEN_RUNS) -> None:
        self._tracer = tracer
        self._max_open_runs = max_open_runs
        self._open: dict[str, _OpenTrace] = {}

    async def emit(self, event: Event) -> None:
        """Fold one event into its run's trace. Never suspends: no round trip is awaited."""
        match event.payload:
            case RunStarted() as started:
                self._start(event, started)
            case RunResumed():
                self._continue(event)
            case ToolCallStarted() as call:
                self._tool_started(event, call)
            case ToolCallCompleted() as call:
                self._tool_completed(event, call)
            case NodeUpdated() as node:
                self._node(event, node)
            case UsageReported() as reported:
                self._usage(event, reported)
            case RunCompleted() as completed:
                self._finish(event, output=_render(completed.output), usage=completed.usage)
            case RunFailed() as failed:
                self._finish(
                    event,
                    level="ERROR",
                    status=f"{failed.error_code}: {failed.message}",
                    metadata={"error_code": failed.error_code, "retryable": failed.retryable},
                )
            case RunCancelled() as cancelled:
                self._finish(event, level="WARNING", status=cancelled.reason or "cancelled")
            case RunInterrupted() as interrupted:
                # A suspended run is closed rather than held open: its answer may take days
                # and may arrive in another process, and a trace nobody can see until then is
                # worse than one that says "waiting". The resume opens a second root in the
                # same trace, so both halves stay together.
                self._finish(
                    event,
                    status=f"waiting for {interrupted.reason}",
                    metadata={"interrupt_id": interrupted.interrupt_id, "suspended": True},
                )
            case RunPaused() as paused:
                self._finish(event, status=paused.reason or "paused", metadata={"suspended": True})
            case _:
                # Deltas, messages, artifacts, custom kinds and anything a newer writer added:
                # a trace is not a transcript, and the event log is what holds the full record.
                pass

    def _start(self, event: Event, started: RunStarted) -> None:
        context = started.context
        budget = context.budget
        self._track(
            event.run_id,
            self._tracer.root(
                started.invocable,
                kind=_OBSERVATION_OF.get(started.kind_of_invocable, "span"),
                trace_key=event.run_id,
                session_id=event.session_id,
                user_id=context.principal,
                input=_render(started.input),
                metadata=_without_nones(
                    {
                        "run_id": event.run_id,
                        "tenant": event.tenant,
                        "invocable_kind": started.kind_of_invocable,
                        "trace_id": context.trace_id,
                        "parent_run_id": started.parent_run_id,
                        "triggered_by": context.triggered_by,
                        "budget_usd": budget.max_usd if budget is not None else None,
                        "budget_tokens": budget.max_tokens if budget is not None else None,
                    }
                ),
            ),
        )

    def _continue(self, event: Event) -> None:
        """Reopen the trace of a run that was suspended, as a second root under the same key.

        The kind and the run's constants live in ``run.started``, which a process that only
        picked up the resume never saw — so a continuation is a plain span and says who it
        belongs to in its metadata.
        """
        if event.run_id in self._open:
            return
        self._track(
            event.run_id,
            self._tracer.root(
                event.origin,
                kind="span",
                trace_key=event.run_id,
                session_id=event.session_id,
                user_id=None,
                metadata={"run_id": event.run_id, "tenant": event.tenant, "resumed": True},
            ),
        )

    def _tool_started(self, event: Event, call: ToolCallStarted) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        open_trace.calls[call.call_id] = open_trace.root.child(call.tool, kind="tool", input=call.args)

    def _tool_completed(self, event: Event, call: ToolCallCompleted) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        # No open span means the dispatch dropped the ``started`` event: record the result on
        # a span of its own rather than losing the call, and accept that it has no duration.
        span = open_trace.calls.pop(call.call_id, None) or open_trace.root.child(call.tool, kind="tool")
        span.finish(
            output=call.result_preview,
            metadata=_without_nones(
                {
                    "result_size": call.result_size,
                    "result_sha256": call.result_sha256,
                    "artifact_id": call.artifact_id,
                }
            ),
            level="ERROR" if call.error else None,
            status=call.error,
        )

    def _node(self, event: Event, node: NodeUpdated) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        # The patched key names, never their values: which node ran and what it touched is
        # what makes a workflow trace readable, and the state itself is not telemetry's to copy.
        open_trace.root.child(node.node, kind="span").finish(output=sorted(node.state_patch))

    def _usage(self, event: Event, reported: UsageReported) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        open_trace.root.child(reported.model, kind="generation").finish(usage=reported.usage)

    def _finish(
        self,
        event: Event,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        status: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        open_trace = self._open.pop(event.run_id, None)
        if open_trace is None:
            return
        for call_id, span in open_trace.calls.items():
            span.finish(level="WARNING", status=f"no completion event for call {call_id}")
        if usage is not None:
            # Langfuse accounts tokens and cost on generations only, so the run's own total
            # gets a generation of its own. Reported per-call usage is already counted by the
            # generations those events opened, hence the distinct name rather than a second
            # copy of a model's.
            open_trace.root.child("run.usage", kind="generation").finish(usage=usage)
        open_trace.root.finish(output=output, metadata=metadata, level=level, status=status)

    def _track(self, run_id: str, root: Observation) -> None:
        while len(self._open) >= self._max_open_runs:
            stale_id = next(iter(self._open))
            stale = self._open.pop(stale_id)
            logger.warning("langfuse trace for run %s abandoned: %d runs already open", stale_id, self._max_open_runs)
            for span in stale.calls.values():
                span.finish(level="WARNING", status="run abandoned")
            stale.root.finish(level="WARNING", status="no terminal event seen")
        self._open[run_id] = _OpenTrace(root)


def _render(blocks: Input) -> list[str]:
    """Content blocks as strings a trace can carry: bytes are described, never copied."""
    out: list[str] = []
    for block in blocks:
        match block:
            case TextBlock():
                out.append(block.text)
            case ImageBlock():
                out.append(f"<image {block.media_type}, {len(block.data_b64)} base64 chars>")
            case ResourceBlock():
                out.append(f"<resource {block.uri}>")
    return out


def _without_nones(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if value is not None}


__all__ = ["MAX_OPEN_RUNS", "LangfuseSink"]
