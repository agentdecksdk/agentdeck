"""``EventSinkPort`` that renders the canonical event stream as Langfuse traces.

One run is one trace: ``run.started`` opens the root observation, a terminal event closes
it, and what happens in between becomes children  -  tool calls as spans, reported model
usage as generations, workflow node updates as points on the timeline. Nothing here asks
whether the run was an agent or a workflow, which is exactly why both are traced by the
same code: the stream is all there is to read.

Two properties of the sink contract shape the whole module. ``emit`` must return promptly,
so every call does in-memory work only  -  the Langfuse SDK buffers observations and ships
them from its own background thread, and nothing here awaits a round trip. And the tap is
lossy: any event can be missing, so an observation left open by a lost ``completed`` event
is closed by the next one that implies it rather than waited for, and a run whose terminal
event never arrives is eventually evicted instead of leaking its spans forever.

Shutdown is where the buffering is paid for: ``close`` finishes what is still open and flushes
the SDK's queue itself, because a batch the SDK has not shipped yet leaves the process only if
something asks it to.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentdeck.core.content import DataBlock, ImageBlock, ResourceBlock, TextBlock
from agentdeck.core.events import (
    ControlObserved,
    ControlRequested,
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

# The same bound per run, for the same reason: a long, chatty run that keeps losing
# ``tool.call.completed`` would otherwise hold one unfinished span per loss, arguments and all.
MAX_OPEN_CALLS = 256

# What an invocable's kind means to Langfuse. A skill is a tool call by another name.
_OBSERVATION_OF: dict[str, ObservationKind] = {"agent": "agent", "workflow": "chain", "skill": "tool"}

# A base64 data URI anywhere in an input or an output is decoded by the Langfuse SDK and
# queued for upload to its media store, so one gets described here instead. Bytes leaving for
# a third party are this adapter's decision to make, not a default to inherit.
_DATA_URI = re.compile(
    r"data:(?P<media_type>[\w.+-]+/[\w.+-]+)?(?:;[\w.+-]+=[\w.+-]+)*;base64,(?P<data>[A-Za-z0-9+/=]+)"
)


@dataclass(slots=True)
class _OpenTrace:
    """A run's root observation and the tool calls still open under it."""

    root: Observation
    calls: dict[str, Observation] = field(default_factory=dict)


class LangfuseSink(EventSinkPort):
    """Renders each run as a Langfuse trace. Registered only when Langfuse is configured."""

    def __init__(
        self,
        tracer: Tracer,
        *,
        max_open_runs: int = MAX_OPEN_RUNS,
        max_open_calls: int = MAX_OPEN_CALLS,
    ) -> None:
        self._tracer = tracer
        self._max_open_runs = max_open_runs
        self._max_open_calls = max_open_calls
        self._open: dict[str, _OpenTrace] = {}
        # Outlives the trace it belongs to, because a suspended run's trace closes and its
        # continuation still has to say whose run it is. Bounded like ``_open``, and dropped
        # once the run reaches a terminal event.

    async def emit(self, event: Event) -> None:
        """Fold one event into its run's trace. Never suspends: no round trip is awaited."""
        match event.payload:
            case RunStarted() as started:
                self._start(event, started)
            case RunResumed() as resumed:
                self._continue(event, resumed)
            case ControlRequested() as requested:
                self._control(event, f"{requested.verb} requested", requested.reason)
            case ControlObserved() as observed:
                self._control(event, f"{observed.verb} observed", f"at {observed.safe_point}")
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

    async def close(self) -> None:
        """Close the traces still open and push the SDK's buffer out before the process ends.

        Both halves matter. An observation nobody finished is never shipped at all, so a run the
        shutdown cut short would otherwise vanish rather than show up as interrupted. And what
        the SDK has already batched only leaves on a flush  -  the one it does at interpreter exit
        never happens to a process that is killed, which is precisely when the telemetry of the
        last few seconds is worth having.
        """
        if self._open:
            logger.warning("%d langfuse traces were still open at shutdown", len(self._open))
        for open_trace in self._open.values():
            self._abandon(open_trace, "the process shut down before the run ended")
        self._open.clear()
        # On a worker thread because the SDK's flush blocks: on the event loop it would block the
        # very deadline that is supposed to keep a slow flush from holding shutdown open.
        await asyncio.to_thread(self._tracer.flush)

    def _start(self, event: Event, started: RunStarted) -> None:
        self._track(
            event.run_id,
            self._tracer.root(
                started.invocable,
                kind=_OBSERVATION_OF.get(started.kind_of_invocable, "span"),
                trace_key=event.run_id,
                session_id=event.session_id,
                input=_render(started.input),
                metadata=_without_nones(
                    {
                        "run_id": event.run_id,
                        "namespace": event.namespace,
                        "invocable_kind": started.kind_of_invocable,
                    }
                ),
            ),
        )

    def _continue(self, event: Event, resumed: RunResumed) -> None:
        """Reopen the trace of a run that was suspended, as a second root under the same key.

        The kind and the run's constants live in ``run.started``, which a process that only
        picked up the resume never saw  -  so a continuation is a plain span and says who it
        belongs to in its metadata.

        The answer the resume carried is the continuation's input, media-described like any
        other content: a trace of an approval flow that does not say what was approved is a
        trace of the wrong half of the story.
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
                input=_render(resumed.value) if resumed.value is not None else None,
                metadata={"run_id": event.run_id, "namespace": event.namespace, "resumed": True},
            ),
        )

    def _control(self, event: Event, what: str, detail: str | None) -> None:
        """A control phase as a point on the run's timeline.

        Both phases are recorded, because the gap between them is the number an operator
        actually asks about  -  "I pressed cancel and it kept going" is answered by when the run
        reached a safe point, not by when the signal was written. A signal that arrives while
        no trace is open here (another process opened it, or the tap dropped the opening) is
        left to the process that has one, the same way tool spans are.
        """
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        open_trace.root.child(event.kind, kind="span").finish(output=what, status=detail)

    def _tool_started(self, event: Event, call: ToolCallStarted) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        while len(open_trace.calls) >= self._max_open_calls:
            stale_id = next(iter(open_trace.calls))
            _no_completion(open_trace.calls.pop(stale_id), stale_id)
        open_trace.calls[call.call_id] = open_trace.root.child(call.tool, kind="tool", input=_without_media(call.args))

    def _tool_completed(self, event: Event, call: ToolCallCompleted) -> None:
        open_trace = self._open.get(event.run_id)
        if open_trace is None:
            return
        # No open span means the dispatch dropped the ``started`` event: record the result on
        # a span of its own rather than losing the call, and accept that it has no duration.
        span = open_trace.calls.pop(call.call_id, None) or open_trace.root.child(call.tool, kind="tool")
        span.finish(
            output=_without_media(call.result_preview),
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
            _no_completion(span, call_id)
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
            logger.warning("langfuse trace for run %s abandoned: %d runs already open", stale_id, self._max_open_runs)
            self._abandon(self._open.pop(stale_id), "no terminal event seen")
        if (superseded := self._open.pop(run_id, None)) is not None:
            # One opening per run is the Runtime's promise; a second one would otherwise leave
            # the first root open forever, invisible in Langfuse and unaccounted for here.
            logger.warning("langfuse trace for run %s reopened; the first one is abandoned", run_id)
            self._abandon(superseded, "superseded by a second opening of this run")
        self._open[run_id] = _OpenTrace(root)

    def _abandon(self, open_trace: _OpenTrace, status: str) -> None:
        for call_id, span in open_trace.calls.items():
            _no_completion(span, call_id)
        open_trace.root.finish(level="WARNING", status=status)


def _no_completion(span: Observation, call_id: str) -> None:
    span.finish(level="WARNING", status=f"no completion event for call {call_id}")


def _render(blocks: Input) -> list[str]:
    """Content blocks as strings a trace can carry: bytes are described, never copied."""
    out: list[str] = []
    for block in blocks:
        match block:
            case TextBlock():
                out.append(_without_media(block.text))
            case ImageBlock():
                out.append(f"<image {block.media_type}, {len(block.data_b64)} base64 chars>")
            case ResourceBlock():
                out.append(f"<resource {block.uri}>")
            case DataBlock():
                out.append(json.dumps(_without_media(block.data), sort_keys=True))
            case _:
                # Reachable since UnknownBlock (#109): a block type this version doesn't know
                # lands here instead of rejecting the whole event. A dropped block would read
                # as "the run had no input", which is worse than a placeholder saying what was
                # lost, so this names the type even though it can't render the content.
                out.append(f"<{getattr(block, 'type', 'unknown')} block>")
    return out


def _without_media(value: Any) -> Any:
    """``value`` with every inline base64 payload replaced by a description of it.

    Tool arguments and results are engine-shaped data this adapter passes through, and the
    Langfuse SDK decodes a data URI it finds in one and queues the bytes for upload to its
    media store. A run that hands a tool an inline image would ship that image; describing it
    keeps the same promise the run's own content blocks get.
    """
    match value:
        case str():
            return _DATA_URI.sub(_describe_media, value)
        case dict():
            return {key: _without_media(item) for key, item in value.items()}
        case list():
            return [_without_media(item) for item in value]
        case tuple():
            return tuple(_without_media(item) for item in value)
        case _:
            return value


def _describe_media(match: re.Match[str]) -> str:
    media_type = match.group("media_type") or "application/octet-stream"
    return f"<inline {media_type}, {len(match.group('data'))} base64 chars>"


def _without_nones(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if value is not None}


__all__ = ["MAX_OPEN_CALLS", "MAX_OPEN_RUNS", "LangfuseSink"]
