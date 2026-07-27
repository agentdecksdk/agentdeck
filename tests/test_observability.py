"""trace_run session identity: a spy OTel exporter, no live Langfuse backend."""

import textwrap
import uuid

import pytest

pytest.importorskip("langfuse", reason="needs the [observability] extra")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult  # noqa: E402

from agentdeck.runtime import observability  # noqa: E402
from agentdeck.runtime.capture import Capture, CaptureActor  # noqa: E402
from agentdeck.runtime.observability import trace_run  # noqa: E402

AGENT_PY = """
from agentdeck.agents import BaseAgent

class Greeter(BaseAgent):
    instructions = "Greet the user."
"""


class SpyExporter(SpanExporter):
    """Collects every ended span in-process instead of shipping it anywhere."""

    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


@pytest.fixture
def spy(monkeypatch):
    """A real Langfuse client wired to a spy exporter instead of the network.

    ``get_client()`` (used by ``trace_run``) picks the sole registered instance when
    there's exactly one; ``LangfuseResourceManager.reset()`` clears the process-wide
    registry so a leftover client from another test can't turn this into "multiple
    projects" and disable tracing.
    """
    from langfuse import Langfuse
    from langfuse._client.resource_manager import LangfuseResourceManager

    LangfuseResourceManager.reset()
    exporter = SpyExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    Langfuse(
        public_key=f"test-{uuid.uuid4()}",
        secret_key="test-secret",
        host="http://localhost:1",
        tracer_provider=provider,
    )
    monkeypatch.setattr(observability, "_initialized", True)
    yield exporter
    LangfuseResourceManager.reset()


def _attr(span, key):
    return span.attributes.get(key)


def test_chat_session_id_lands_on_the_root_trace(spy):
    """App.chat(..., session_id="wa-123", ...)'s session id must reach the root span."""
    with trace_run(None, name="Greeter", kind="agent", input="hi", session_id="wa-123"):
        pass
    (span,) = spy.spans
    assert _attr(span, "session.id") == "wa-123"


def test_session_id_wins_over_capture_derived_identity(spy):
    capture = Capture(session_id="cap-session", author_id=None, actor=CaptureActor.USER)
    with trace_run(capture, name="Greeter", kind="agent", input="hi", session_id="wa-123"):
        pass
    (span,) = spy.spans
    assert _attr(span, "session.id") == "wa-123"


def test_capture_session_id_is_the_fallback_when_no_session_id_given(spy):
    capture = Capture(session_id="cap-session", author_id=None, actor=CaptureActor.USER)
    with trace_run(capture, name="Greeter", kind="agent", input="hi"):
        pass
    (span,) = spy.spans
    assert _attr(span, "session.id") == "cap-session"


def test_session_less_run_traces_without_error_or_session(spy):
    """session=None (no App session) must still trace cleanly, with a null session."""
    with trace_run(None, name="Greeter", kind="agent", input="hi") as tr:
        tr.set_output("hello")
    (span,) = spy.spans
    assert _attr(span, "session.id") is None


def test_nested_run_does_not_repropagate_identity(spy):
    """A nested unit (skill/sub-agent) inherits the root's session; passing a different
    session_id at the nested call must not override it (no re-propagation, same as today).
    """
    with (
        trace_run(None, name="Greeter", kind="agent", input="hi", session_id="wa-123"),
        trace_run(None, name="skill", kind="tool", input="x", session_id="different-session"),
    ):
        pass
    root, nested = spy.spans[1], spy.spans[0]
    assert _attr(root, "session.id") == "wa-123"
    assert _attr(nested, "session.id") == "wa-123"


async def test_app_chat_turn_produces_root_trace_with_chat_session_id(spy, tmp_path, monkeypatch):
    """End-to-end: App.chat(..., session_id=...) reaches the root trace via HeadlessRunner."""
    import sys
    from types import SimpleNamespace

    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    async def fake_run(agent, message, **kwargs):
        return SimpleNamespace(final_output=f"echo:{message}", context_wrapper=None)

    monkeypatch.setattr("agentdeck.agents.runners.headless.Runner.run", fake_run)

    app = App()
    result = await app.chat("Greeter", "wa-123", "hi")

    assert result.final_output == "echo:hi"
    (span,) = spy.spans
    assert _attr(span, "session.id") == "wa-123"
