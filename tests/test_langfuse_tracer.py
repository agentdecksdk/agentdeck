"""The SDK half of the Langfuse sink, against a spy exporter instead of a Langfuse backend.

``tests/test_langfuse_sink.py`` pins the mapping; this file pins the translation of that
mapping into real Langfuse spans — the trace id, the nesting, and the attributes the backend
reads. Needs the ``[observability]`` extra, so it skips where the SDK is not installed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("langfuse", reason="needs the [observability] extra")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult  # noqa: E402

from agentdeck.adapters.engines.stub import StubEngine, stub_spec  # noqa: E402
from agentdeck.adapters.stores.memory import MemoryEventStore  # noqa: E402
from agentdeck.adapters.telemetry.langfuse import LangfuseSink, LangfuseTracer, langfuse_sink  # noqa: E402
from agentdeck.core.content import TextBlock  # noqa: E402
from agentdeck.core.context import RunContext  # noqa: E402
from agentdeck.core.events import (  # noqa: E402
    NodeUpdated,
    RunCompleted,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageReported,
)
from agentdeck.core.invocable import InvocableKind  # noqa: E402
from agentdeck.runtime.service import Runtime  # noqa: E402
from agentdeck.runtime.settings import LangfuseSettings  # noqa: E402

TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
INPUT = [TextBlock(text="ship it")]
CTX = RunContext(tenant="acme", principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")
TOTAL = Usage(input_tokens=11, output_tokens=7, usd=0.02)
SHA = "ab" * 32
WORKFLOW = (
    NodeUpdated(node="plan", state_patch={"plan": "search"}),
    ToolCallStarted(call_id="c1", tool="search", args={"q": "agentdeck"}),
    ToolCallCompleted(call_id="c1", tool="search", result_preview="3 hits", result_size=6, result_sha256=SHA),
    UsageReported(model="gpt-4o", usage=TOTAL),
    RunCompleted(output=[TextBlock(text="shipped")], usage=TOTAL),
)


class SpyExporter(SpanExporter):
    """Collects every ended span in-process instead of shipping it anywhere.

    Also the test's handle on the client and provider the fixture built, so a test can reach
    the SDK the sink is talking to and open a span beside it.
    """

    def __init__(self) -> None:
        self.spans: list = []
        self.client: Any = None
        self.provider: Any = None

    def export(self, spans):  # noqa: ANN001, ANN201 — OTel's own signature
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def by_name(self, name: str):  # noqa: ANN201 — a ReadableSpan, which OTel exposes only as an internal type
        return next(span for span in self.spans if span.name == name)

    def media_queued(self) -> int:
        """How many uploads the SDK has queued for its media store — zero, always, here."""
        return self.client._resources._media_upload_queue.qsize()


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A real Langfuse client whose spans land in a spy exporter, never on the network.

    ``LangfuseResourceManager.reset()`` clears the process-wide client registry so a client
    left behind by another test can't turn this one into "multiple projects"; the bounded OTLP
    timeout keeps the SDK's own exporter from retrying against the closed port for seconds.
    """
    from langfuse import Langfuse
    from langfuse._client.resource_manager import LangfuseResourceManager

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "1")
    LangfuseResourceManager.reset()
    exporter = SpyExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    client = Langfuse(
        public_key=f"pk-lf-{uuid.uuid4()}",
        secret_key="sk-lf-test",
        host="http://localhost:1",
        tracer_provider=provider,
    )
    exporter.client = client
    exporter.provider = provider
    yield exporter
    LangfuseResourceManager.reset()


def _attr(span, key: str):  # noqa: ANN001, ANN201 — reads one OTel attribute off a ReadableSpan
    return span.attributes.get(key)


async def _run(spy, name: str = "Pipeline") -> None:  # noqa: ANN001
    spec = stub_spec(name, *WORKFLOW, kind=InvocableKind.WORKFLOW)
    runtime = Runtime(
        [StubEngine()],
        MemoryEventStore(),
        {spec.name: spec},
        sinks=[LangfuseSink(LangfuseTracer(spy.client))],
        clock=lambda: TS,
    )
    async for _ in runtime.run(name, INPUT, CTX):
        pass
    await runtime.drain()


async def test_a_workflow_run_exports_one_langfuse_trace_with_its_spans_nested(spy) -> None:  # noqa: ANN001
    await _run(spy)

    root = spy.by_name("Pipeline")
    assert {span.context.trace_id for span in spy.spans} == {int(spy.client.create_trace_id(seed="r-1"), 16)}
    assert _attr(root, "langfuse.observation.type") == "chain"
    assert _attr(root, "session.id") == "s-1"
    assert _attr(root, "user.id") == "user:1"
    assert _attr(root, "langfuse.trace.name") == "Pipeline"
    assert _attr(root, "langfuse.trace.metadata.tenant") == "acme"
    for name, kind in [("plan", "span"), ("search", "tool"), ("gpt-4o", "generation")]:
        assert spy.by_name(name).parent.span_id == root.context.span_id
        assert _attr(spy.by_name(name), "langfuse.observation.type") == kind


async def test_a_tool_span_carries_its_preview_and_a_generation_carries_its_usage(spy) -> None:  # noqa: ANN001
    await _run(spy)

    assert _attr(spy.by_name("search"), "langfuse.observation.output") == "3 hits"
    assert _attr(spy.by_name("search"), "langfuse.observation.metadata.result_sha256") == SHA
    assert _attr(spy.by_name("run.usage"), "langfuse.observation.usage_details") == '{"input": 11, "output": 7}'
    assert _attr(spy.by_name("run.usage"), "langfuse.observation.cost_details") == '{"total": 0.02}'


async def test_the_trace_belongs_to_the_run_not_to_whatever_span_was_current(spy) -> None:  # noqa: ANN001
    """The sink emits from the dispatch's consumer task, which inherited its context from a
    run — so a v1-instrumented process must not end up with this trace nested under an
    unrelated span, and a run's trace id must stay a function of the run alone.
    """
    with spy.provider.get_tracer("unrelated").start_as_current_span("someone-elses-span") as ambient:
        await _run(spy)

    root = spy.by_name("Pipeline")
    assert root.context.trace_id != ambient.context.trace_id
    assert root.context.trace_id == int(spy.client.create_trace_id(seed="r-1"), 16)


async def test_an_inline_data_uri_in_tool_args_never_reaches_the_langfuse_media_store(spy) -> None:  # noqa: ANN001
    """The egress the sink disclaims, checked at the SDK itself: handed a base64 data URI, the
    Langfuse SDK decodes it and queues an upload to its media API. The sink must never hand it one.
    """
    payload = "A" * 3000
    data_uri = f"data:image/png;base64,{payload}"
    spec = stub_spec(
        "Describer",
        ToolCallStarted(call_id="c1", tool="describe", args={"img": data_uri}),
        ToolCallCompleted(call_id="c1", tool="describe", result_preview="a cat", result_size=5, result_sha256=SHA),
        RunCompleted(output=[TextBlock(text="a cat")], usage=TOTAL),
        kind=InvocableKind.WORKFLOW,
    )
    runtime = Runtime(
        [StubEngine()],
        MemoryEventStore(),
        {spec.name: spec},
        sinks=[LangfuseSink(LangfuseTracer(spy.client))],
        clock=lambda: TS,
    )
    async for _ in runtime.run("Describer", INPUT, CTX):
        pass
    await runtime.drain()

    assert spy.media_queued() == 0
    assert payload not in str(_attr(spy.by_name("describe"), "langfuse.observation.input"))

    # The same URI handed over unredacted does queue an upload — which is what makes the two
    # assertions above mean something rather than merely pass.
    unredacted = LangfuseTracer(spy.client).root("Probe", kind="span", trace_key="r-2", session_id=None, user_id=None)
    unredacted.child("describe", kind="tool", input={"img": data_uri}).finish()
    unredacted.finish()
    assert spy.media_queued() > 0


def test_configured_keys_yield_a_sink_over_the_real_sdk(spy) -> None:  # noqa: ANN001
    """The unconfigured half lives in ``test_langfuse_sink.py``; this is the other branch."""
    sink = langfuse_sink(
        LangfuseSettings(public_key="pk-lf-configured", secret_key="sk-lf-test", host="http://localhost:1")
    )

    assert isinstance(sink, LangfuseSink)
