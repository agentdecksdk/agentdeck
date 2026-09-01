"""Issue #47 end to end: a real tool reporting, through a real surface.

The two claims worth proving outside the Runtime's own unit tests: an openai-agents **function
tool** finds the reporter on the SDK's context object, and an SSE client sees its reports arrive
in order without the surface knowing they exist. The reference CLI renderer reading them closes
the loop, including its default case  -  the promise every consumer makes about a kind it has
never heard of.

Scripted fakes only: the SDK boundary is the one thing stubbed, so nothing here calls a model.
"""

from __future__ import annotations

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
from agentdeck.core.events import Event, Reported, RunCompleted, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.core.workers import SyncToolWorkers
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.events import KnownPayload

pytest.importorskip("fastapi")

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
    # The ceiling, asserted rather than only documented (``core/reporting.py``): the reports are
    # drained at the engine's *next* payload, and this SDK emits both of a tool call's item
    # events only once the tool has returned  -  so a report made inside the call surfaces just
    # ahead of ``tool.call.started``, not during the call. Ordered and inside the run either
    # way; if this list ever changes, the drain's granularity changed with it.
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


# --- the SSE surface and the reference renderer ----------------------------------------


class _ReportingStub(StubExecutor):
    """A scripted run that reports between its payloads  -  the surface must not care which
    engine did it, so the cheapest one is the honest choice here."""

    async def execute(
        self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        ctx.reporter.info("Searching GitHub")
        ctx.reporter.report("issues_reviewed", current=2, total=4)
        async for payload in super().execute(spec, input, history, ctx):
            yield payload


def _runtime() -> Runtime:
    done = RunCompleted(output=coerce_input("two issues, both open"), usage=Usage(input_tokens=1, output_tokens=1))
    spec = stub_spec("Searcher", done)
    return Runtime([_ReportingStub()], MemoryEventStore(), {spec.name: spec})


def _event(payload: KnownPayload) -> Event:
    return Event(
        kind=payload.kind,
        seq=0,
        run_id="r-1",
        session_id="s-1",
        namespace="acme",
        origin="Searcher",
        ts=TS,
        payload=payload,
    )
