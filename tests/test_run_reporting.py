"""Issue #47 end to end: a real tool reporting, through a real surface.

The claims worth proving outside the Runtime's own unit tests: an openai-agents **function
tool** finds the reporter on the SDK's context object, a sync ``@tool``'s reports preserve order
against its own return, and a report is written to the log when it fires (#487) rather than
batched at return: an ordering guarantee held under a suspended append, a storm of 200 reports, a
report made after the run closed or after its loop closed, and a run that reports and then fails.

Scripted fakes only: the SDK boundary is the one thing stubbed, so nothing here calls a model.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from agents import Agent, RunContextWrapper, function_tool
from agents.models.interface import Model
from event_log_checks import check_contiguous, check_terminal
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck import ToolCtx, tool
from agentdeck.adapters.executors.native import NativeExecutor
from agentdeck.adapters.executors.openai_agents import ExecutionStore, OpenAIAgentsExecutor
from agentdeck.adapters.executors.stub import StubExecutor, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.events import Event, Reported
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.core.workers import SyncToolWorkers
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.events import KnownPayload
    from agentdeck.core.reporting import Reporter

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
CTX = RunContext(namespace="acme", run_id="r-1", session_id="s-1")


def _reports(events: Sequence[Event]) -> list[tuple[str, Any]]:
    """The reported events, as (level, message, fields)  -  the shape assertions read on."""
    return [
        (event.payload.level, event.payload.message, event.payload.fields)
        for event in events
        if isinstance(event.payload, Reported)
    ]


# --- an openai-agents function tool ----------------------------------------------------

_USAGE = ResponseUsage(
    input_tokens=3,
    input_tokens_details=InputTokensDetails(cached_tokens=0),
    output_tokens=2,
    output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
    total_tokens=5,
)


class _CallsTheToolOnce(Model):
    """Calls ``search_github`` on the first turn, answers once its result is in the tail."""

    async def stream_response(self, _instructions: Any = None, input: Any = None, *_a: Any, **_k: Any) -> AsyncIterator:
        last = input[-1] if input else None
        called = isinstance(last, dict) and last.get("type") == "function_call_output"
        output: list[Any] = (
            [
                ResponseOutputMessage(
                    id="msg_1",
                    content=[ResponseOutputText(annotations=[], text="two issues, both open", type="output_text")],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]
            if called
            else [
                ResponseFunctionToolCall(
                    id="fc_1", call_id="call_1", name="search_github", arguments="{}", type="function_call"
                )
            ]
        )
        yield ResponseCompletedEvent(
            response=Response(
                id="resp_1",
                created_at=0.0,
                model="fake-reporting",
                object="response",
                output=output,
                parallel_tool_calls=False,
                tool_choice="auto",
                tools=[],
                usage=_USAGE,
            ),
            sequence_number=0,
            type="response.completed",
        )

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError("this test only streams")


@function_tool
async def search_github(wrapper: RunContextWrapper[RunContext]) -> str:
    """Search GitHub, reporting what it is doing while it does."""
    wrapper.context.reporter.info("Searching GitHub")
    wrapper.context.reporter.report("issues_reviewed", current=2, total=4)
    return "two open issues"


async def test_a_function_tool_reports_through_the_sdk_context() -> None:
    """A tool six frames inside the SDK, with no Runtime in sight: the run context arrives as
    the SDK's own context object, and the reporter on it is the whole reach."""
    agent = Agent(name="Searcher", instructions="use the tool", tools=[search_github], model=_CallsTheToolOnce())
    spec = InvocableSpec(name="Searcher", kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=agent)
    store = MemoryEventStore()
    runtime = Runtime([OpenAIAgentsExecutor(ExecutionStore())], store, {spec.name: spec})

    events = [
        event
        async for event in runtime.run(
            "Searcher",
            coerce_input("what is open?"),
            session_id=(CTX).session_id,
            namespace=(CTX).namespace,
        )
    ]

    assert _reports(events) == [
        ("info", "Searching GitHub", {}),
        ("record", "issues_reviewed", {"current": 2, "total": 4}),
    ]
    # Written when made, so both reports are in the log before this SDK emits either of the tool
    # call's item events (it emits them once the tool has returned). What a consumer reading this
    # generator sees is still handed to it at the engine's next payload, which is why they sit
    # ahead of ``tool.call.started`` here rather than between it and its completion.
    assert [event.kind for event in events] == [
        "run.started",
        # One per finished model call, which is what makes two turns visible as two: the
        # terminal event's usage is the turn's cumulative total and cannot tell them apart.
        "usage.reported",
        "report",
        "report",
        "tool.call.started",
        "tool.call.completed",
        "usage.reported",
        "message.completed",
        "run.completed",
    ]
    assert check_contiguous(events) == [] and check_terminal(events) is None
    assert await store.read_session(CTX) == events
    assert status_of(events) is RunStatus.COMPLETED


# --- a sync @tool body, on its worker thread ----------------------------------------------


@tool
def _noisy_sync(ctx: ToolCtx) -> str:
    """Report twice, then return  -  a plain synchronous call, same as from any other body."""
    ctx.reporter.info("one")
    ctx.reporter.info("two")
    return "done"


async def test_a_sync_tools_reports_preserve_order_against_its_own_return() -> None:
    """Two reports made before a sync tool's return show up before ``run.completed``, not
    silently dropped the way an unawaited coroutine would be."""
    spec = InvocableSpec(name="_noisy_sync", kind=InvocableKind.TOOL, executor=NativeExecutor.name, native=_noisy_sync)
    runtime = Runtime([NativeExecutor(workers=SyncToolWorkers())], MemoryEventStore(), {spec.name: spec})

    events = [event async for event in runtime.run("_noisy_sync", coerce_input(""))]

    assert [event.kind for event in events] == ["run.started", "report", "report", "run.completed"]
    assert _reports(events) == [("info", "one", {}), ("info", "two", {})]


# --- written when it is made, not when the call ends (#487) --------------------------------


class _ReportGate(MemoryEventStore):
    """A store that says when a report's append has settled, and can hold the first one inside
    itself: the gated-append shape ``tests/contract/test_store.py`` uses to hold a window open
    rather than race for it. A refusal settles too, since a refused report has also ended."""

    def __init__(self, *, hold_first: bool = False) -> None:
        super().__init__()
        self.settled = 0
        self.holding = asyncio.Event()
        self.release = asyncio.Event()
        self._hold = hold_first
        self._done = asyncio.Condition()

    async def append(self, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        reported = any(isinstance(payload, Reported) for payload in payloads)
        if reported and self._hold:
            self._hold = False
            self.holding.set()
            await self.release.wait()
        try:
            return await super().append(payloads, ctx, origin)
        finally:
            if reported:
                async with self._done:
                    self.settled += 1
                    self._done.notify_all()

    async def after(self, reports: int) -> None:
        """Return once ``reports`` report appends have ended, written or refused."""
        async with self._done:
            await self._done.wait_for(lambda: self.settled >= reports)


def _reporting_runtime(store: MemoryEventStore, native: Any, name: str) -> Runtime:
    spec = InvocableSpec(name=name, kind=InvocableKind.TOOL, executor=NativeExecutor.name, native=native)
    return Runtime([NativeExecutor(workers=SyncToolWorkers())], store, {spec.name: spec})


async def _played(runtime: Runtime, name: str, run_id: str) -> list[Event]:
    return [event async for event in runtime.run(name, coerce_input(""), namespace="acme", run_id=run_id)]


async def test_a_report_made_inside_a_long_call_is_in_the_log_before_the_call_returns() -> None:
    """The window held open rather than raced for: the call cannot return until the test has read
    its report out of the log, so a report that waits for the next payload fails by timing out."""
    store = _ReportGate()
    read = asyncio.Event()

    @tool
    async def _long_call(ctx: ToolCtx) -> str:
        """Report, then block until the test has read that report back out of the log."""
        ctx.reporter.info("halfway")
        await read.wait()
        return "done"

    runtime = _reporting_runtime(store, _long_call, "_long_call")
    ctx = RunContext(namespace="acme", run_id="r-long")

    async def reading() -> list[str]:
        await asyncio.wait_for(store.after(1), timeout=5)
        mid_call = [event.kind for event in await store.read_run(ctx)]
        read.set()
        return mid_call

    played, mid_call = await asyncio.gather(_played(runtime, "_long_call", "r-long"), reading())

    assert mid_call == ["run.started", "report"]
    assert [event.kind for event in played] == ["run.started", "report", "run.completed"]
    assert _reports(played) == [("info", "halfway", {})]


async def test_a_report_made_after_the_run_closed_is_refused_and_says_so_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The store refuses it (#471) instead of the reporter guessing it would, and a backlog
    behind that refusal costs one log line, not one per report. Nothing reaches the loop's
    exception handler either: a fire-and-forget append that raised past its own coroutine would
    only ever be ``never retrieved`` noise."""
    store = _ReportGate()
    reporters: list[Reporter] = []

    @tool
    async def _hands_back_its_reporter(ctx: ToolCtx) -> str:
        """Keep the run's reporter reachable after the run has closed."""
        reporters.append(ctx.reporter)
        return "done"

    runtime = _reporting_runtime(store, _hands_back_its_reporter, "_hands_back_its_reporter")
    ctx = RunContext(namespace="acme", run_id="r-late")

    with caplog.at_level(logging.DEBUG):
        played = await _played(runtime, "_hands_back_its_reporter", "r-late")
        for n in range(50):
            reporters[0].report("too_late", n=n)
        await asyncio.wait_for(store.after(1), timeout=5)
        await asyncio.sleep(0)

    assert [event.kind for event in played] == ["run.started", "run.completed"]
    assert [event.kind for event in await store.read_run(ctx)] == ["run.started", "run.completed"]
    assert caplog.text.count("is closed; the report it made") == 1
    assert "never retrieved" not in caplog.text
    assert [record.message for record in caplog.records if record.levelno >= logging.ERROR] == []


async def test_a_report_storm_lands_whole_and_leaves_the_terminal_event_last() -> None:
    """A tight reporting loop is the case the 64-deep buffer was bounded against, and where it
    dropped reports 64 to 199. All 200 are in the log now, in order, and the event that closes
    the run is still the last row: what the run reports delays its own closing, never displaces
    it, and never outlives it."""
    store = _ReportGate()

    @tool
    def _storming(ctx: ToolCtx) -> str:
        """Report 200 times from a worker thread, then return."""
        for n in range(200):
            ctx.reporter.report("step", n=n)
        return "done"

    runtime = _reporting_runtime(store, _storming, "_storming")
    ctx = RunContext(namespace="acme", run_id="r-storm")

    played = await _played(runtime, "_storming", "r-storm")
    logged = await store.read_run(ctx)

    assert logged[0].kind == "run.started" and logged[-1].kind == "run.completed"
    assert await store.run_status(ctx) is RunStatus.COMPLETED
    assert [event.kind for event in played][-1] == "run.completed"
    assert [event.payload.fields["n"] for event in logged if isinstance(event.payload, Reported)] == list(range(200))


async def test_two_reports_keep_their_order_when_the_first_append_suspends_inside_the_store() -> None:
    """What the writer's lock buys, held open rather than raced for: the first report is inside
    ``append`` when the second is made. Unordered appends would put the second one first."""
    store = _ReportGate(hold_first=True)
    both = asyncio.Event()

    @tool
    async def _two_reports(ctx: ToolCtx) -> str:
        """Report once into a store that holds it, report again, then wait for both to land."""
        ctx.reporter.info("first")
        await asyncio.wait_for(store.holding.wait(), timeout=5)
        ctx.reporter.info("second")
        # Scheduler turns, not seconds: an append that does not wait its turn lands in one of
        # these, ahead of the report still suspended inside the store.
        for _ in range(5):
            await asyncio.sleep(0)
        store.release.set()
        await both.wait()
        return "done"

    runtime = _reporting_runtime(store, _two_reports, "_two_reports")
    ctx = RunContext(namespace="acme", run_id="r-order")

    async def releasing() -> None:
        await asyncio.wait_for(store.after(2), timeout=5)
        both.set()

    played, _ = await asyncio.gather(_played(runtime, "_two_reports", "r-order"), releasing())

    assert [event.kind for event in await store.read_run(ctx)] == [
        "run.started",
        "report",
        "report",
        "run.completed",
    ]
    assert _reports(played) == [("info", "first", {}), ("info", "second", {})]


def test_a_report_made_after_the_loop_that_played_the_run_closed_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A context can outlive the loop it was bound on. The writer then has nowhere to schedule
    the append, and an emitter still must not be handed an exception for reporting."""
    reporters: list[Reporter] = []

    @tool
    async def _outlives_its_loop(ctx: ToolCtx) -> str:
        """Keep the run's reporter reachable after the loop that played the run is gone."""
        reporters.append(ctx.reporter)
        return "done"

    async def play() -> None:
        runtime = _reporting_runtime(_ReportGate(), _outlives_its_loop, "_outlives_its_loop")
        await _played(runtime, "_outlives_its_loop", "r-gone")

    asyncio.run(play())

    with caplog.at_level(logging.WARNING):
        reporters[0].info("the loop is gone")

    assert "reported after its loop closed" in caplog.text


async def test_a_run_that_reports_and_then_fails_still_ends_on_run_failed() -> None:
    """The arm that ends a run the engine did not end itself owes the log the same order the
    terminal-payload arm does: five reports fired before the raise are in front of ``run.failed``,
    so a run that merely failed cannot read as one resurrected past its own terminal event."""
    store = _ReportGate()

    @tool
    def _reports_then_raises(ctx: ToolCtx) -> str:
        """Report five times from a worker thread, then fail."""
        for n in range(5):
            ctx.reporter.report("step", n=n)
        raise RuntimeError("the tool gave up")

    runtime = _reporting_runtime(store, _reports_then_raises, "_reports_then_raises")
    ctx = RunContext(namespace="acme", run_id="r-failing")

    streamed: list[str] = []
    with pytest.raises(RuntimeError, match="gave up"):
        async for event in runtime.run("_reports_then_raises", coerce_input(""), namespace="acme", run_id="r-failing"):
            streamed.append(event.kind)

    logged = await store.read_run(ctx)
    assert streamed == ["run.started", *["report"] * 5, "run.failed"]
    assert [event.kind for event in logged] == streamed
    assert check_terminal(logged) is None and check_contiguous(logged) == []


async def test_a_run_whose_engine_just_stops_keeps_its_reports_in_front_of_the_failure() -> None:
    """The other arm that closes a run for its engine, and the same order: an engine that ends
    without a terminal event has one written for it, behind whatever the run already reported."""
    store = _ReportGate()

    class _ReportsThenStops(StubExecutor):
        async def execute(
            self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
        ) -> AsyncGenerator[KnownPayload, None]:
            ctx.reporter.info("about to stop saying anything")
            return
            yield  # pragma: no cover  -  an engine that yields nothing is still a generator

    spec = stub_spec("Stopper")
    runtime = Runtime([_ReportsThenStops()], store, {spec.name: spec})
    ctx = RunContext(namespace="acme", run_id="r-stopped")

    streamed = [
        event.kind async for event in runtime.run("Stopper", coerce_input(""), namespace="acme", run_id="r-stopped")
    ]

    assert streamed == ["run.started", "report", "run.failed"]
    assert [event.kind for event in await store.read_run(ctx)] == streamed
    assert check_terminal(await store.read_run(ctx)) is None
