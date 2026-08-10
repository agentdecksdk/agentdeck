"""The Langfuse sink, proved against a local collector — no SDK, no keys, no network.

The collector keeps the observation tree the sink builds instead of shipping it, which is
what makes the mapping assertable: a trace is a shape, and these tests state that shape for
a workflow run, an agent run, a suspended run and every way one can end. The events come
from a real ``Runtime`` and a real bounded fan-out, so what is asserted is what a run would
actually hand this sink.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.telemetry.langfuse import LangfuseSink, langfuse_sink
from agentdeck.adapters.telemetry.langfuse.sink import _render
from agentdeck.core.content import DataBlock, ImageBlock, TextBlock, UnknownBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    ControlObserved,
    ControlRequested,
    Event,
    NodeUpdated,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    RunPaused,
    RunResumed,
    RunStarted,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageReported,
)
from agentdeck.core.invocable import InvocableKind
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import LangfuseSettings

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import pytest

    from agentdeck.adapters.telemetry.langfuse.trace import Level, ObservationKind
    from agentdeck.core.events import KnownPayload

TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
INPUT = [TextBlock(text="ship it")]
CTX = RunContext(
    namespace="acme",
    run_id="r-1",
    session_id="s-1",
)
SECRET = "hunter2"
TOTAL = Usage(input_tokens=11, output_tokens=7, usd=0.02)
SHA = "ab" * 32
PAYLOAD = "A" * 3000
DATA_URI = f"data:image/png;base64,{PAYLOAD}"
DESCRIBED = "<inline image/png, 3000 base64 chars>"

# A workflow's shape: a node runs, it calls a tool, a model is billed, the run answers.
WORKFLOW: tuple[KnownPayload, ...] = (
    NodeUpdated(node="plan", state_patch={"plan": "search then answer", "api_key": SECRET}),
    ToolCallStarted(call_id="c1", tool="search", args={"q": "agentdeck"}),
    ToolCallCompleted(call_id="c1", tool="search", result_preview="3 hits", result_size=4096, result_sha256=SHA),
    UsageReported(model="gpt-4o", usage=TOTAL),
    TextDelta(message_id="m1", text="shipped"),
    RunCompleted(output=[TextBlock(text="shipped")], usage=TOTAL),
)


@dataclass
class Observed:
    """One recorded observation — what would have become a Langfuse span."""

    name: str
    kind: str
    input: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_key: str | None = None
    session_id: str | None = None
    children: list[Observed] = field(default_factory=list)
    output: Any = None
    level: str | None = None
    status: str | None = None
    usage: Usage | None = None
    finishes: int = 0

    def child(
        self,
        name: str,
        *,
        kind: ObservationKind,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observed:
        assert not self.finishes, f"{self.name!r} opened a child after it was finished"
        recorded = Observed(name=name, kind=kind, input=input, metadata=dict(metadata or {}))
        self.children.append(recorded)
        return recorded

    def finish(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        status: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        self.finishes += 1
        self.output = output
        self.metadata |= dict(metadata or {})
        self.level = level
        self.status = status
        self.usage = usage

    def walk(self) -> Iterator[Observed]:
        yield self
        for child in self.children:
            yield from child.walk()

    def named(self, name: str) -> Observed:
        return next(observed for observed in self.walk() if observed.name == name)

    def shape(self) -> list[tuple[str, str]]:
        return [(child.name, child.kind) for child in self.children]


@dataclass
class Collector:
    """Stands in for Langfuse: every trace opened, in memory, in the order it was opened."""

    traces: list[Observed] = field(default_factory=list)
    flushes: int = 0
    unfinished_at_flush: list[int] = field(default_factory=list)

    def root(
        self,
        name: str,
        *,
        kind: ObservationKind,
        trace_key: str,
        session_id: str | None,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observed:
        recorded = Observed(
            name=name,
            kind=kind,
            input=input,
            metadata=dict(metadata or {}),
            trace_key=trace_key,
            session_id=session_id,
        )
        self.traces.append(recorded)
        return recorded

    def flush(self) -> None:
        """Stands in for the SDK's blocking flush, and records what a real one could not ship:
        an observation nobody finished is not in the buffer being flushed."""
        self.flushes += 1
        self.unfinished_at_flush.append(sum(1 for observed in self.everything() if not observed.finishes))

    def only(self) -> Observed:
        assert len(self.traces) == 1, f"expected one trace, got {[trace.name for trace in self.traces]}"
        return self.traces[0]

    def everything(self) -> list[Observed]:
        return [observed for trace in self.traces for observed in trace.walk()]


def _runtime(collector: Collector, *steps: KnownPayload, kind: InvocableKind, name: str) -> Runtime:
    spec = stub_spec(name, *steps, kind=kind)
    return Runtime(
        [StubEngine()],
        MemoryEventStore(),
        {spec.name: spec},
        sinks=[LangfuseSink(collector)],
    )


async def _traced(
    *steps: KnownPayload, kind: InvocableKind = InvocableKind.WORKFLOW, name: str = "Pipeline"
) -> Collector:
    """Play a run through a real Runtime and hand back what the sink made of it."""
    collector = Collector()
    runtime = _runtime(collector, *steps, kind=kind, name=name)
    async for _ in runtime.run(
        name, INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    await runtime.drain()
    return collector


def _event(payload: KnownPayload, *, run_id: str = "r-1", seq: int = 0) -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id=run_id,
        session_id="s-1",
        namespace="acme",
        origin="Pipeline",
        ts=TS,
        payload=payload,
    )


def _started(run_id: str = "r-1") -> Event:
    return _event(
        RunStarted(
            invocable="Pipeline",
            kind_of_invocable="workflow",
            input=INPUT,
        ),
        run_id=run_id,
    )


async def test_a_workflow_run_becomes_one_trace_carrying_its_nodes_tools_and_usage() -> None:
    """The point of reading the stream: a workflow, which v1's runner-level tracing never saw."""
    trace = (await _traced(*WORKFLOW)).only()

    assert (trace.name, trace.kind) == ("Pipeline", "chain")
    assert (trace.trace_key, trace.session_id) == ("r-1", "s-1")
    assert trace.input == ["ship it"]
    assert trace.output == ["shipped"]
    assert trace.metadata["namespace"] == "acme"
    assert trace.metadata["invocable_kind"] == "workflow"
    assert trace.shape() == [
        ("plan", "span"),
        ("search", "tool"),
        ("gpt-4o", "generation"),
        ("run.usage", "generation"),
    ]
    assert [observed.finishes for observed in trace.walk()] == [1, 1, 1, 1, 1]


async def test_an_agent_run_takes_the_same_path_and_only_changes_the_trace_kind() -> None:
    """Same code, same shape, whatever engine produced the events — that is the whole claim."""
    workflow = (await _traced(*WORKFLOW)).only()
    agent = (await _traced(*WORKFLOW, kind=InvocableKind.AGENT, name="Greeter")).only()

    assert (workflow.kind, agent.kind) == ("chain", "agent")
    assert agent.shape() == workflow.shape()
    assert agent.metadata["invocable_kind"] == "agent"


async def test_a_tool_span_carries_the_preview_size_and_hash_and_never_the_result() -> None:
    call = (await _traced(*WORKFLOW)).only().named("search")

    assert call.input == {"q": "agentdeck"}
    assert call.output == "3 hits"
    assert call.metadata == {"result_size": 4096, "result_sha256": SHA}
    assert call.level is None


async def test_usage_reaches_the_trace_as_generation_metrics() -> None:
    """Langfuse only accounts tokens and cost on generations, so both totals must be one."""
    trace = (await _traced(*WORKFLOW)).only()

    assert trace.named("gpt-4o").usage == TOTAL
    assert trace.named("run.usage").usage == TOTAL
    assert trace.usage is None


async def test_a_node_span_names_the_keys_it_patched_and_never_their_values() -> None:
    trace = (await _traced(*WORKFLOW)).only()

    assert trace.named("plan").output == ["api_key", "plan"]
    assert SECRET not in repr((await _traced(*WORKFLOW)).everything())


async def test_streamed_text_never_becomes_an_observation_of_its_own() -> None:
    """A trace is not a transcript: the run's own output is the record of what it said."""
    trace = (await _traced(*WORKFLOW)).only()

    assert all(child.name != "m1" for child in trace.children)
    assert trace.output == ["shipped"]


async def test_image_bytes_are_described_to_the_trace_never_copied_into_it() -> None:
    collector = Collector()
    runtime = _runtime(collector, RunCompleted(output=INPUT, usage=TOTAL), kind=InvocableKind.AGENT, name="Viewer")
    async for _ in runtime.run(
        "Viewer",
        [ImageBlock(media_type="image/png", data_b64="AAAA")],
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    ):
        pass
    await runtime.drain()

    assert collector.only().input == ["<image image/png, 4 base64 chars>"]
    assert "AAAA" not in repr(collector.everything())


async def test_an_inline_data_uri_in_tool_arguments_is_described_never_handed_over() -> None:
    """The Langfuse SDK decodes a base64 data URI it is handed and queues the bytes for upload
    to its media store, so a run calling an image or document tool with an inline payload would
    ship that payload to a third party — the one thing this sink promises not to do.
    """
    collector = await _traced(
        ToolCallStarted(call_id="c1", tool="describe", args={"img": DATA_URI, "seen": [{"again": DATA_URI}]}),
        ToolCallCompleted(
            call_id="c1", tool="describe", result_preview=f"got {DATA_URI}", result_size=9, result_sha256=SHA
        ),
        RunCompleted(output=[TextBlock(text="a cat")], usage=TOTAL),
    )
    call = collector.only().named("describe")

    assert call.input == {"img": DESCRIBED, "seen": [{"again": DESCRIBED}]}
    assert call.output == f"got {DESCRIBED}"
    assert PAYLOAD not in repr(collector.everything())


def test_a_block_kind_this_version_cannot_render_is_still_mentioned() -> None:
    """The default case earns its keep: a newer writer's block type must not make a run read
    as one with no input at all. Real now that ``UnknownBlock`` (#109) exists — this is no
    longer a stand-in for a shape ``ContentBlock`` couldn't hold."""
    block = UnknownBlock(type="video", raw_block={"type": "video", "uri": "s3://clip.mp4"})
    assert _render([block]) == ["<video block>"]


async def test_structured_data_reaches_the_trace_as_json_on_both_ends() -> None:
    """A workflow's state is the run's real input and output — a trace that dropped the block
    would read as a run with nothing to say."""
    collector = Collector()
    final = {"claim_id": "7777", "decision": "approved"}
    runtime = _runtime(
        collector,
        RunCompleted(output=[DataBlock(data=final)], usage=TOTAL),
        kind=InvocableKind.WORKFLOW,
        name="Pipeline",
    )
    async for _ in runtime.run(
        "Pipeline",
        [DataBlock(data={"input": "claim 7777"})],
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    ):
        pass
    await runtime.drain()

    trace = collector.only()
    assert trace.input == ['{"input": "claim 7777"}']
    assert trace.output == ['{"claim_id": "7777", "decision": "approved"}']


async def test_an_inline_data_uri_inside_structured_data_is_described_too() -> None:
    collector = Collector()
    runtime = _runtime(collector, RunCompleted(output=INPUT, usage=TOTAL), kind=InvocableKind.AGENT, name="Viewer")
    async for _ in runtime.run(
        "Viewer",
        [DataBlock(data={"page": {"img": DATA_URI}})],
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    ):
        pass
    await runtime.drain()

    assert collector.only().input == [f'{{"page": {{"img": "{DESCRIBED}"}}}}']
    assert PAYLOAD not in repr(collector.everything())


async def test_an_inline_data_uri_in_a_run_input_is_described_too() -> None:
    collector = Collector()
    runtime = _runtime(collector, RunCompleted(output=INPUT, usage=TOTAL), kind=InvocableKind.AGENT, name="Viewer")
    async for _ in runtime.run(
        "Viewer",
        [TextBlock(text=f"look at {DATA_URI}")],
        run_id=(CTX).run_id,
        session_id=(CTX).session_id,
        namespace=(CTX).namespace,
    ):
        pass
    await runtime.drain()

    assert collector.only().input == [f"look at {DESCRIBED}"]
    assert PAYLOAD not in repr(collector.everything())


async def test_a_failed_run_closes_its_trace_at_error_level() -> None:
    trace = (await _traced(RunFailed(error_code="tool_error", message="search is down", retryable=True))).only()

    assert trace.level == "ERROR"
    assert trace.status == "tool_error: search is down"
    assert trace.metadata["retryable"] is True
    assert trace.finishes == 1


async def test_a_tool_that_errored_marks_its_own_span_and_not_the_trace() -> None:
    collector = await _traced(
        ToolCallStarted(call_id="c1", tool="search", args={}),
        ToolCallCompleted(
            call_id="c1", tool="search", result_preview="", result_size=0, result_sha256=SHA, error="timeout"
        ),
        RunCompleted(output=[TextBlock(text="recovered")], usage=TOTAL),
    )
    trace = collector.only()

    assert (trace.named("search").level, trace.named("search").status) == ("ERROR", "timeout")
    assert trace.level is None


async def test_an_interrupted_run_ships_its_half_and_the_resume_continues_the_same_trace() -> None:
    """A run waiting on a human must be visible while it waits: a span held open until the
    answer arrives — possibly in another process, possibly never — is a trace nobody can see.
    """
    collector = Collector()
    runtime = _runtime(
        collector,
        NodeUpdated(node="plan", state_patch={"plan": "ask"}),
        RunInterrupted(interrupt_id="i1", reason="approval", payload={}, thread_id="t1"),
        RunCompleted(output=[TextBlock(text="shipped")], usage=TOTAL),
        kind=InvocableKind.WORKFLOW,
        name="Approver",
    )
    async for _ in runtime.run(
        "Approver", INPUT, run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    async for _ in runtime.resume(
        "Approver", "t1", "approved", run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    await runtime.drain()

    waiting, continued = collector.traces
    assert waiting.trace_key == continued.trace_key == "r-1"
    assert (waiting.status, waiting.metadata["suspended"], waiting.metadata["interrupt_id"]) == (
        "waiting for approval",
        True,
        "i1",
    )
    assert waiting.shape() == [("plan", "span")]
    assert continued.metadata["resumed"] is True
    assert continued.output == ["shipped"]
    # The run's constants are only in ``run.started``; a continuation that lost them would leave half a
    # run's cost unattributed to whoever asked for it.
    assert [observed.finishes for observed in collector.everything()] == [1, 1, 1, 1]


async def test_a_process_that_only_saw_the_resume_still_traces_it_under_the_run_s_own_key() -> None:
    """The other half of a cross-process resume: no ``run.started`` means no kind and no
    constants to stamp, but the trace key is the run, so both halves still meet in one trace.
    """
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_event(RunResumed(reason=None)))
    root = collector.only()

    assert (root.trace_key, root.kind) == ("r-1", "span")
    assert root.metadata["resumed"] is True
    assert root.input is None  # a resume that answered nothing has no input to show


async def test_a_continuation_shows_the_answer_the_run_was_resumed_with() -> None:
    """A trace of an approval flow that doesn't say what was approved is a trace of the wrong
    half of the story — and the answer is content, so it is media-described like any other."""
    sink = LangfuseSink(collector := Collector())
    await sink.emit(
        _event(RunResumed(reason="approved", value=[TextBlock(text=DATA_URI), DataBlock(data={"approved": True})]))
    )

    assert collector.only().input == [DESCRIBED, '{"approved": true}']


async def test_both_control_phases_land_on_the_run_s_timeline() -> None:
    """The gap between them is the number an operator actually asks about: "I pressed cancel
    and it kept going" is answered by when the run reached a safe point."""
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_started())
    await sink.emit(_event(ControlRequested(verb="cancel", reason="operator asked"), seq=1))
    await sink.emit(_event(ControlObserved(verb="cancel", safe_point="tool_dispatch"), seq=2))
    trace = collector.only()

    assert trace.shape() == [("control.requested", "span"), ("control.observed", "span")]
    assert (trace.named("control.requested").output, trace.named("control.requested").status) == (
        "cancel requested",
        "operator asked",
    )
    assert (trace.named("control.observed").output, trace.named("control.observed").status) == (
        "cancel observed",
        "at tool_dispatch",
    )
    assert [observed.finishes for observed in collector.everything()] == [0, 1, 1]


async def test_a_control_signal_with_no_trace_open_is_left_to_the_process_that_has_one() -> None:
    """A signal can be written by a process that never saw the run open — the same position a
    tool result whose dispatch this sink missed is in."""
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_event(ControlRequested(verb="pause")))

    assert collector.traces == []


async def test_a_tool_call_whose_completion_never_arrives_is_closed_when_the_run_ends() -> None:
    """The tap is lossy, so an open span has to be closed by what follows it, not waited for."""
    trace = (
        await _traced(
            ToolCallStarted(call_id="c1", tool="search", args={}),
            RunCompleted(output=[TextBlock(text="shipped")], usage=TOTAL),
        )
    ).only()

    assert trace.named("search").finishes == 1
    assert trace.named("search").level == "WARNING"
    assert "c1" in str(trace.named("search").status)


async def test_a_tool_result_whose_dispatch_arrives_first_still_reaches_the_trace() -> None:
    """A dropped ``tool.call.started`` must cost the span's duration, not the call itself."""
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_started())
    await sink.emit(
        _event(
            ToolCallCompleted(call_id="c1", tool="search", result_preview="3 hits", result_size=6, result_sha256=SHA),
            seq=1,
        )
    )

    assert collector.only().shape() == [("search", "tool")]
    assert collector.only().named("search").output == "3 hits"


async def test_a_run_whose_terminal_event_was_dropped_cannot_hold_its_trace_open_forever() -> None:
    """Five hundred abandoned runs is a leak; a bounded, reported one is a lossy tap."""
    sink = LangfuseSink(collector := Collector(), max_open_runs=1)
    await sink.emit(_started("r-1"))
    await sink.emit(_started("r-2"))

    abandoned, current = collector.traces
    assert abandoned.trace_key == "r-1"
    assert (abandoned.finishes, abandoned.level, abandoned.status) == (1, "WARNING", "no terminal event seen")
    assert current.finishes == 0


async def test_a_run_that_keeps_losing_tool_completions_cannot_pile_up_open_spans() -> None:
    """The run map is bounded; a chatty run's open calls have to be too, or one long run
    accumulates a never-finished span per lost completion, serialized arguments and all.
    """
    sink = LangfuseSink(collector := Collector(), max_open_calls=1)
    await sink.emit(_started())
    await sink.emit(_event(ToolCallStarted(call_id="c1", tool="search", args={}), seq=1))
    await sink.emit(_event(ToolCallStarted(call_id="c2", tool="search", args={}), seq=2))

    first, second = collector.only().children
    assert (first.finishes, first.level, first.status) == (1, "WARNING", "no completion event for call c1")
    assert second.finishes == 0


async def test_a_second_opening_of_one_run_abandons_the_root_it_replaces() -> None:
    """One ``run.started`` per run is the Runtime's promise; a replay of one must not leave a
    root open forever, invisible in Langfuse and uncounted here.
    """
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_started())
    await sink.emit(_started())

    first, second = collector.traces
    assert (first.finishes, first.level) == (1, "WARNING")
    assert first.status == "superseded by a second opening of this run"
    assert second.finishes == 0


async def test_an_event_kind_this_version_does_not_know_is_skipped_not_crashed_on() -> None:
    sink = LangfuseSink(collector := Collector())
    unknown = Event.model_validate(
        {**_started().model_dump(mode="json"), "kind": "run.teleported", "payload": {"kind": "run.teleported"}}
    )
    await sink.emit(_started())
    await sink.emit(unknown)

    assert unknown.payload.kind == "run.teleported"
    assert collector.only().children == []


async def test_no_arm_of_the_mapping_ever_suspends_so_none_can_spend_the_dispatch_deadline() -> None:
    """A sink awaiting a round trip per event would burn its emit budget and be disabled after
    five; completing inside a zero-length deadline is what proves no wait happens at all.

    Every arm, not one: a whole run, each ending, a continuation, an eviction and an unknown
    kind all go through the same deadline, because a single event would only clear one arm.
    """
    endings: tuple[KnownPayload, ...] = (
        RunCompleted(output=[TextBlock(text="shipped")], usage=TOTAL),
        RunFailed(error_code="engine_error", message="boom", retryable=False),
        RunCancelled(reason="consumer stopped reading"),
        RunInterrupted(interrupt_id="i1", reason="human", payload={}, thread_id="t1"),
        RunPaused(reason="waiting on a timer"),
    )
    unknown = Event.model_validate(
        {**_started().model_dump(mode="json"), "kind": "run.teleported", "payload": {"kind": "run.teleported"}}
    )

    async with asyncio.timeout(0):
        whole_run = LangfuseSink(Collector())
        for seq, payload in enumerate((_started().payload, *WORKFLOW)):
            await whole_run.emit(_event(payload, seq=seq))
        for ending in endings:
            sink = LangfuseSink(Collector())
            await sink.emit(_started())
            await sink.emit(_event(ending, seq=1))
        resumed = LangfuseSink(Collector())
        await resumed.emit(_event(RunResumed(reason="approved", value=[TextBlock(text="approved")])))
        await resumed.emit(unknown)
        controlled = LangfuseSink(Collector())
        await controlled.emit(_started())
        await controlled.emit(_event(ControlRequested(verb="pause", reason="operator"), seq=1))
        await controlled.emit(_event(ControlObserved(verb="pause", safe_point="node_boundary"), seq=2))
        evicting = LangfuseSink(Collector(), max_open_runs=1, max_open_calls=1)
        await evicting.emit(_started("r-1"))
        await evicting.emit(_event(ToolCallStarted(call_id="c1", tool="search", args={}), run_id="r-1", seq=1))
        await evicting.emit(_event(ToolCallStarted(call_id="c2", tool="search", args={}), run_id="r-1", seq=2))
        await evicting.emit(_started("r-2"))


async def test_a_shutdown_ships_the_buffer_instead_of_leaving_it_to_the_sdk_s_atexit() -> None:
    """The SDK batches and ships from its own thread, so a run that finishes seconds before the
    process dies is only in that buffer — and the exit hook meant to flush it never runs when the
    process is killed outright."""
    collector = await _traced(*WORKFLOW)

    assert collector.flushes == 1  # once, at the close, not once per event
    assert collector.only().finishes == 1


async def test_a_run_still_open_at_shutdown_is_closed_so_its_trace_can_be_shipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An observation nobody finished is never shipped at all, so flushing is not enough on its
    own: a run the shutdown cut short has to end as interrupted rather than vanish."""
    caplog.set_level(logging.WARNING, logger="agentdeck.adapters.telemetry.langfuse.sink")
    sink = LangfuseSink(collector := Collector())
    await sink.emit(_started())
    await sink.emit(_event(ToolCallStarted(call_id="c1", tool="search", args={}), seq=1))

    await sink.close()

    trace = collector.only()
    assert (trace.finishes, trace.level) == (1, "WARNING")
    assert trace.status == "the process shut down before the run ended"
    assert trace.named("search").finishes == 1
    assert collector.unfinished_at_flush == [0]  # everything was closed before the buffer shipped
    assert "1 langfuse traces were still open at shutdown" in [record.getMessage() for record in caplog.records]


def test_langfuse_without_keys_yields_no_sink_to_register() -> None:
    assert langfuse_sink(LangfuseSettings(public_key="", secret_key="")) is None
    assert langfuse_sink(LangfuseSettings(public_key="pk-lf-1", secret_key="")) is None


def test_an_unconfigured_process_never_even_imports_the_langfuse_sdk() -> None:
    """A subprocess, because in-process ``sys.modules`` cannot unsee an import another test
    made — and "no overhead when unconfigured" has to mean the SDK was never loaded.
    """
    probe = (
        "import sys;"
        "from agentdeck.adapters.telemetry.langfuse import langfuse_sink;"
        "from agentdeck.runtime.settings import LangfuseSettings;"
        "assert langfuse_sink(LangfuseSettings(public_key='', secret_key='')) is None;"
        "assert 'langfuse' not in sys.modules, sorted(m for m in sys.modules if 'langfuse' in m);"
        "print('no sink, no sdk')"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr
    assert "no sink, no sdk" in done.stdout
