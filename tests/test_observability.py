"""Observers on a Deck's event stream: when they start, what they see, and who closes what.

The Langfuse SDK is stubbed at its one construction seam
(``adapters.telemetry.langfuse.client.build_client``), so every assertion here holds without
the ``[observability]`` extra, without a key that reaches anything, and without a network.
That seam is also the subject: #162's first defect was two modules constructing a client, and
counting constructions is how these tests can tell.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

import pytest

from agentdeck import observers
from agentdeck.adapters.telemetry.langfuse import client as langfuse_client
from agentdeck.core.ports import EventSinkPort
from agentdeck.errors import ConfigError
from agentdeck.observers import Langfuse
from agentdeck.runtime.settings import LangfuseSettings, reset_settings_cache
from agentdeck.testing import ScriptedModel, patch_model

AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user.")
"""

# A workflow whose node drives an agent of its own — the shape that used to export two trace
# trees, because the node's runner started the Agents-SDK instrumentation on its way past.
AGENT_FLOW_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.authoring import AgentNode, Workflow
from agentdeck_project.agents.greeter.agent import greeter


class State(BaseModel):
    input: str = ""
    output: str = ""


def _build_graph():
    g = StateGraph(State)
    g.add_node("greet", AgentNode(greeter, input_key="input", output_key="output"))
    g.set_entry_point("greet")
    g.add_edge("greet", END)
    return g


chat_flow = Workflow(name="ChatFlow", state=State, graph=_build_graph)
"""


@dataclass
class Opened:
    """One observation the sink opened — what would have become a Langfuse span."""

    name: str
    kind: str
    session_id: str | None = None
    children: list[Opened] = field(default_factory=list)

    def child(self, name: str, *, kind: str, input: Any = None, metadata: Any = None) -> Opened:  # noqa: A002, ARG002 — the Tracer port's own signature
        opened = Opened(name=name, kind=kind)
        self.children.append(opened)
        return opened

    def finish(self, **_kwargs: Any) -> None:
        """What a span carries when it closes is the sink's business, tested there."""


@dataclass
class RecordingTracer:
    """Stands in for the Langfuse SDK: every root the sink opened, in memory."""

    roots: list[Opened] = field(default_factory=list)
    flushes: int = 0

    def root(self, name: str, *, kind: str, session_id: str | None, **_kwargs: Any) -> Opened:
        opened = Opened(name=name, kind=kind, session_id=session_id)
        self.roots.append(opened)
        return opened

    def flush(self) -> None:
        self.flushes += 1


class Recorder(EventSinkPort):
    """A caller's own observer: what it saw, and the lifecycle calls it was given."""

    def __init__(self) -> None:
        self.kinds: list[str] = []
        self.starts = 0
        self.closes = 0

    async def start(self) -> None:
        self.starts += 1

    async def emit(self, event: Any) -> None:
        self.kinds.append(event.kind)

    async def close(self) -> None:
        self.closes += 1


class Bare(EventSinkPort):
    """An observer written before ``start()`` existed: only the abstract method."""

    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def emit(self, event: Any) -> None:
        self.kinds.append(event.kind)


class Exploding(EventSinkPort):
    """A caller's observer that fails on every event. The run must not notice."""

    def __init__(self) -> None:
        self.attempts = 0

    async def emit(self, event: Any) -> None:  # noqa: ARG002 — it never gets as far as reading one
        self.attempts += 1
        raise RuntimeError("this sink is broken")


@dataclass
class Telemetry:
    """The stubbed SDK: what the settings-derived sink recorded, and every client built."""

    tracer: RecordingTracer
    clients: list[object]

    @property
    def roots(self) -> list[Opened]:
        return self.tracer.roots


@pytest.fixture
def telemetry(monkeypatch):
    """Swap the one client-construction seam for a spy, and the tracer for a recorder."""
    clients: list[object] = []

    def _build(_settings):
        client = object()
        clients.append(client)
        return client

    tracer = RecordingTracer()
    monkeypatch.setattr(langfuse_client, "build_client", _build)
    monkeypatch.setattr(langfuse_client, "LangfuseTracer", lambda _client: tracer)
    return Telemetry(tracer=tracer, clients=clients)


@pytest.fixture
def langfuse_keys(monkeypatch):
    """Set the two keys that decide whether Langfuse is configured at all.

    The settings cache is cleared on the way out as well as in: ``monkeypatch`` restores the
    environment but not an ``lru_cache``, and a leaked one would leave every later test in
    this process running with Langfuse on.
    """

    def _set(public_key: str = "pk-lf-test", secret_key: str = "sk-lf-test") -> None:
        monkeypatch.setenv("AGENTDECK_LANGFUSE_PUBLIC_KEY", public_key)
        monkeypatch.setenv("AGENTDECK_LANGFUSE_SECRET_KEY", secret_key)
        reset_settings_cache()

    yield _set
    reset_settings_cache()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(textwrap.dedent(AGENT_PY))
    (root / "workflows" / "agent_flow").mkdir(parents=True)
    (root / "workflows" / "agent_flow" / "workflow.py").write_text(textwrap.dedent(AGENT_FLOW_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    return tmp_path


@pytest.fixture
def scripted():
    with patch_model(ScriptedModel(deltas=("hi",))):
        yield


# --- build() validates the shape, and touches no network --------------------------------------


async def test_build_with_langfuse_configured_builds_no_client_and_opens_no_socket(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    monkeypatch,
):
    """``build()`` stays a CI-runnable check with telemetry configured.

    Two assertions, because either alone is weak: no client was constructed, and no socket was
    opened by anything at all — the network ban is what makes ``build()`` safe to run where
    Langfuse is unreachable, and a client that quietly resolved DNS would still break that.
    """
    import socket

    from agentdeck.deck import Deck

    langfuse_keys()
    deck = Deck.from_project()

    def _no_sockets(*_args, **_kwargs):
        raise AssertionError("build() opened a socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)
    monkeypatch.setattr(socket, "create_connection", _no_sockets)
    deck.build()

    assert telemetry.clients == []
    await deck.aclose()


async def test_build_refuses_something_that_is_not_an_observer(project):  # noqa: ARG001 — the project dir is what `from_project()` discovers
    from agentdeck.deck import Deck

    deck = Deck.from_project(observers=[object()])

    with pytest.raises(ConfigError, match="observers= takes EventSinkPort instances"):
        deck.build()
    await deck.aclose()


def test_build_runtime_wires_no_telemetry_of_its_own():
    """The assembly seam never resolves Langfuse — the root of #162's first defect.

    It used to: ``sinks=None`` meant "read the keys and build a client", so a client existed
    before any caller had said whether it wanted one, and the *second* construction (the one
    that carried the span filter) was silently discarded by the SDK's per-public-key cache.
    Keys are deliberately set here — with them set, the old wiring imported the SDK and built a
    client; the new one must not. A fresh interpreter, because this one has already imported
    half the world.
    """
    import os

    probe = (
        "import sys;"
        "from agentdeck.adapters.engines.stub import StubEngine;"
        "from agentdeck.composition import build_runtime;"
        "runtime = build_runtime(engines=[StubEngine()], invocables={});"
        "assert runtime._sinks == (), runtime._sinks;"
        "assert 'langfuse' not in sys.modules, sorted(m for m in sys.modules if 'langfuse' in m);"
        "print('configured, and still no client')"
    )
    keyed = {"AGENTDECK_LANGFUSE_PUBLIC_KEY": "pk-lf-test", "AGENTDECK_LANGFUSE_SECRET_KEY": "sk-lf-test"}
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, env={**os.environ, **keyed}
    )

    assert done.returncode == 0, done.stderr
    assert "configured, and still no client" in done.stdout


# --- the lifecycle: once, at open, before any run ----------------------------------------------


async def test_the_telemetry_client_is_built_once_at_open_and_never_during_a_run(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """One client for the whole life of a deck, built when it opens.

    The count is the assertion #162 needs: two constructions was the bug, and the SDK caches
    per public key so the second one's arguments are discarded rather than applied. Runs are
    counted too, since the old ordering started tracing on the *first run* instead.
    """
    from agentdeck.deck import Deck

    langfuse_keys()
    deck = Deck.from_project()

    deck.build()
    assert telemetry.clients == []

    async with deck:
        assert len(telemetry.clients) == 1
        await deck.run("Greeter", "hello", session_id="s-1")
        await deck.run("Greeter", "again", session_id="s-1")
        assert len(telemetry.clients) == 1


async def test_an_open_deck_traces_its_runs_under_their_own_session(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project() as deck:
        result = await deck.run("Greeter", "hello", session_id="wa-123")

    assert result.output == "hi"
    [trace] = telemetry.roots
    assert (trace.name, trace.kind, trace.session_id) == ("Greeter", "agent", "wa-123")


async def test_an_unconfigured_deck_runs_untraced_and_says_nothing(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
    caplog,
):
    """Tracing off is the quiet default: no client, no trace, and nothing warned about it."""
    from agentdeck.deck import Deck

    langfuse_keys("", "")

    with caplog.at_level(logging.WARNING, logger="agentdeck"):
        async with Deck.from_project() as deck:
            await deck.run("Greeter", "hello")

    assert telemetry.clients == []
    assert telemetry.roots == []
    assert [record.message for record in caplog.records if "langfuse" in record.message.lower()] == []


async def test_no_observers_at_all_is_sayable_explicitly(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """``observers=()`` opts out even where the environment configures Langfuse."""
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project(observers=()) as deck:
        await deck.run("Greeter", "hello")

    assert telemetry.clients == []
    assert telemetry.roots == []


# --- the port's own start()/close() lifecycle ---------------------------------------------------


async def test_every_observer_is_started_once_before_any_run(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """``start()`` is on the port, so every observer inherits "opened once, at deck open" rather
    than each one inventing its own moment. Ordering matters as much as the count: an observer
    still unstarted when a run begins is the #181 defect with a different owner."""
    from agentdeck.deck import Deck

    started_before_first_event: list[int] = []

    class Watcher(Recorder):
        async def emit(self, event: Any) -> None:
            if not self.kinds:
                started_before_first_event.append(self.starts)
            await super().emit(event)

    watcher = Watcher()
    deck = Deck.from_project(observers=[watcher])

    deck.build()
    assert watcher.starts == 0, "build() must start nothing"

    async with deck:
        assert watcher.starts == 1
        await deck.run("Greeter", "hello")
        await deck.run("Greeter", "again")

    assert watcher.starts == 1
    assert watcher.closes == 1
    assert started_before_first_event == [1]


async def test_an_observer_predating_start_still_works(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """``start()`` is additive: the port's default is a no-op, so an implementation that only
    defines ``emit`` keeps working rather than failing at open."""
    from agentdeck.deck import Deck

    bare = Bare()

    async with Deck.from_project(observers=[bare]) as deck:
        await deck.run("Greeter", "hello")

    assert "run.completed" in bare.kinds


async def test_an_observer_that_refuses_to_start_refuses_the_open(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
):
    """Naming Langfuse and getting silence is the ghost declaration this project refuses.

    The already-started observer beside it is closed on the way out: there is no ``__aexit__``
    for an ``__aenter__`` that raised, so a client opened before the refusal would otherwise be
    held by nobody.
    """
    from agentdeck.deck import Deck

    langfuse_keys("", "")
    first = Recorder()
    deck = Deck.from_project(observers=[first, Langfuse()])

    with pytest.raises(ConfigError, match="no Langfuse keys are configured"):
        await deck.__aenter__()

    assert (first.starts, first.closes) == (1, 1)
    assert telemetry.clients == []
    await deck.aclose()


async def test_the_langfuse_observer_named_explicitly_traces_the_run(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """``observers=[Langfuse()]`` is the same object the settings path resolves to, so naming it
    beside an observer of your own gets you both rather than a choice between them."""
    from agentdeck.deck import Deck

    langfuse_keys()
    audit = Recorder()

    async with Deck.from_project(observers=[Langfuse(), audit]) as deck:
        await deck.run("Greeter", "hello", session_id="s-11")

    assert len(telemetry.clients) == 1
    assert [(root.name, root.session_id) for root in telemetry.roots] == [("Greeter", "s-11")]
    assert "run.completed" in audit.kinds


def test_constructing_the_langfuse_observer_reads_and_builds_nothing():
    """It is constructible where Langfuse is neither configured nor installed — which is what
    lets ``resolve_observers()`` and a user's own module-level ``Langfuse()`` stay network-free."""
    probe = (
        "import sys;"
        "from agentdeck.observers import Langfuse;"
        "Langfuse();"
        "assert 'langfuse' not in sys.modules, sorted(m for m in sys.modules if 'langfuse' in m);"
        "print('constructed, and still no sdk')"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr
    assert "constructed, and still no sdk" in done.stdout


# --- several observers at once, and who owns them ----------------------------------------------


async def test_every_sink_named_sees_the_same_stream(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """The log is the hub: the Runtime fans one run out to as many taps as it is given."""
    from agentdeck.deck import Deck

    audit, cost = Recorder(), Recorder()

    async with Deck.from_project(observers=[audit, cost]) as deck:
        await deck.run("Greeter", "hello")

    assert "run.started" in audit.kinds and "run.completed" in audit.kinds
    assert audit.kinds == cost.kinds
    assert telemetry.clients == [], "observers= was given; the deck must not also build a Langfuse client"


async def test_naming_observers_suppresses_the_settings_derived_one(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """The ownership rule at construction time: a deck told which taps to open opens those, and
    does not quietly build a Langfuse client beside them because the environment has keys."""
    from agentdeck.deck import Deck

    langfuse_keys()
    audit = Recorder()

    async with Deck.from_project(observers=[audit]) as deck:
        await deck.run("Greeter", "hello")

    assert audit.kinds != []
    assert telemetry.clients == []
    assert telemetry.roots == []


async def test_the_deck_flushes_the_sink_it_built_from_settings(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """The Langfuse SDK ships from a background thread, so the buffer only leaves the process
    if something asks — and the deck that built the client is what asks, as it closes."""
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project() as deck:
        await deck.run("Greeter", "hello")
        assert telemetry.tracer.flushes == 0

    assert telemetry.tracer.flushes == 1


async def test_a_broken_sink_never_breaks_a_run(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """An observer is fire-and-forget by the port's contract, and ``observers=`` is where a
    caller's own code first reaches that contract — so prove it end to end rather than at the
    dispatch's unit boundary: the turn completes, and the log has every event the observer lost."""
    from agentdeck.deck import Deck

    broken, healthy = Exploding(), Recorder()

    async with Deck.from_project(observers=[broken, healthy]) as deck:
        result = await deck.run("Greeter", "hello", session_id="s-3")
        logged = [event.kind async for event in deck.stream("Greeter", "again", session_id="s-3")]

    assert result.output == "hi"
    assert broken.attempts > 0, "the broken sink was never even offered an event"
    assert "run.completed" in logged
    assert "run.completed" in healthy.kinds


# --- #162's second defect: no orphan trees ------------------------------------------------------


async def test_a_workflow_turn_driving_an_agent_exports_exactly_one_trace_root(
    project,  # noqa: ARG001 — the project dir is what `from_project()` discovers
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — points the run at a fake model
):
    """One run, one trace — the count #162's second defect broke.

    A workflow node that drives an agent goes through ``authoring``'s direct-call runner, which
    used to start the Agents-SDK instrumentation and open a root observation of its own. With
    Langfuse on, that exported a second, sessionless tree beside the sink's: one good trace plus
    one orphan. The runner opens nothing now, so the sink's root is the only one, and the
    instrumentation that produced the orphan's spans is never installed.
    """
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project() as deck:
        await deck.run("ChatFlow", {"input": "hello"}, session_id="s-7")

    assert [(root.name, root.kind, root.session_id) for root in telemetry.roots] == [("ChatFlow", "chain", "s-7")]
    assert "openinference.instrumentation.openai_agents" not in sys.modules


def test_only_the_opt_in_observer_instruments_the_agents_sdk():
    """The other half of the orphan fix, pinned by name rather than by count.

    ``OpenAIAgentsInstrumentor().instrument()`` is what put OpenInference spans on the global
    tracer provider with no root to hang off. It is reachable again, but only through
    ``Langfuse(sdk_spans=True)`` — a caller who asked for the raw layer and was told in the
    reference that it arrives as a separate trace.

    What must not come back is a *second* caller: instrumentation is process-global and
    one-way, so one installed from anywhere else would follow every later Deck, including
    decks that asked for the semantic layer alone. Checked by name, because such a caller would
    not fail the count test above — that only covers the paths it exercises — and would not
    fail ``test_sdk_spans_are_off_unless_asked_for`` either, which patches this module's own
    hook.
    """
    found = subprocess.run(
        ["git", "grep", "-l", "OpenAIAgentsInstrumentor", "--", "agentdeck/"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert found.stdout.split() == ["agentdeck/observers.py"], (
        f"the Agents-SDK instrumentor is reachable from {found.stdout.split()}; it belongs to "
        "Langfuse(sdk_spans=True) alone"
    )


# --- the one construction point ------------------------------------------------------------------


def test_the_sdk_client_is_constructed_in_exactly_one_place():
    """#162's first defect, as a rule instead of a symptom.

    The Langfuse SDK caches one resource manager per public key and discards every later
    constructor's arguments, so a second ``Langfuse(...)`` anywhere in the package is not a
    second client and cannot change how the first one filters or exports — which is exactly how
    the ``should_export_span`` filter came to never apply. One construction site, checked by
    name so a second one cannot be added quietly.
    """
    found = subprocess.run(
        ["git", "grep", "-n", "-e", "from langfuse import Langfuse", "--", "agentdeck/"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert [line.split(":")[0] for line in found.stdout.splitlines()] == [
        "agentdeck/adapters/telemetry/langfuse/client.py"
    ]


def test_build_client_names_the_otel_service_and_bounds_the_exporter(monkeypatch):
    """The two process-wide settings that have to happen before the SDK builds its exporter,
    and that a second construction could therefore never apply."""
    import os

    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", raising=False)
    constructed: dict[str, Any] = {}

    class _FakeSdk:
        Langfuse = staticmethod(lambda **kwargs: constructed.update(kwargs) or object())

    monkeypatch.setitem(sys.modules, "langfuse", _FakeSdk)

    langfuse_client.build_client(
        LangfuseSettings(public_key="pk", secret_key="sk", service_name="booking-svc", environment="prod")
    )

    assert os.environ["OTEL_SERVICE_NAME"] == "booking-svc"
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] == "5"
    assert (constructed["public_key"], constructed["environment"]) == ("pk", "prod")


# --- the raw SDK layer, opt-in --------------------------------------------------------------


async def test_sdk_spans_are_off_unless_asked_for(telemetry, langfuse_keys, monkeypatch):
    """Instrumentation is process-global and one-way: once installed it outlives the Deck that
    installed it and follows every later one. So the default has to be *no*, and a test has to
    hold it there — a leak here would not fail the run that caused it.
    """
    langfuse_keys()
    instrumented = []
    monkeypatch.setattr(observers, "instrument_agents_sdk", lambda: instrumented.append(True))

    await observers.Langfuse().start()

    assert instrumented == [], "the default observer instrumented the SDK without being asked"


async def test_sdk_spans_true_instruments_after_the_client_exists(telemetry, langfuse_keys, monkeypatch):
    """Order matters and is asserted, not assumed: the client registers Langfuse's span
    processor on the global OTel provider, and the instrumentation appends to it. Instrumenting
    first would append to a provider Langfuse has not claimed yet.

    Also stops the flag rotting into a no-op — a `sdk_spans=True` that quietly does nothing is
    the ghost declaration this observer refuses everywhere else.
    """
    langfuse_keys()
    order: list[str] = []
    monkeypatch.setattr(observers, "instrument_agents_sdk", lambda: order.append("instrument"))
    real_sink = langfuse_client.langfuse_sink

    def _spy(settings):
        order.append("client")
        return real_sink(settings)

    monkeypatch.setattr(langfuse_client, "langfuse_sink", _spy)

    await observers.Langfuse(sdk_spans=True).start()

    assert order == ["client", "instrument"]
