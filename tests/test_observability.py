"""Observability as a Deck capability: when the client is built, who closes it, what it traces.

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
from scripted_model import ScriptedModel, patch_provider, provider_of

from agentdeck.adapters.telemetry.langfuse import client as langfuse_client
from agentdeck.errors import ConfigError
from agentdeck.observability import Langfuse
from agentdeck.runtime.settings import LangfuseSettings, reset_settings_cache

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

    def shape(self) -> list[tuple[str, str]]:
        return [(child.name, child.kind) for child in self.children]


@dataclass
class RecordingTracer:
    """Stands in for the Langfuse SDK: every root the sink opened, in memory."""

    roots: list[Opened] = field(default_factory=list)

    def root(self, name: str, *, kind: str, session_id: str | None, **_kwargs: Any) -> Opened:
        opened = Opened(name=name, kind=kind, session_id=session_id)
        self.roots.append(opened)
        return opened

    def flush(self) -> None:
        """Nothing to ship."""


class SpyClient:
    """Stands in for a Langfuse SDK client: records whether anything shut it down."""

    def __init__(self) -> None:
        self.shutdowns = 0

    def shutdown(self) -> None:
        self.shutdowns += 1


@dataclass
class Telemetry:
    """The stubbed SDK: what the sink recorded, and every client that was constructed."""

    tracer: RecordingTracer
    clients: list[SpyClient]

    @property
    def roots(self) -> list[Opened]:
        return self.tracer.roots


@pytest.fixture
def telemetry(monkeypatch):
    """Swap the one client-construction seam for a spy, and the tracer for a recorder."""
    clients: list[SpyClient] = []

    def _build(_settings):
        client = SpyClient()
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
def scripted(monkeypatch):
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("hi",))))


# --- configuration, and the network build() must not touch -----------------------------------


def test_build_resolves_the_configuration_without_constructing_a_client(telemetry, langfuse_keys):
    langfuse_keys()

    settings = Langfuse().build()

    assert (settings.public_key, settings.secret_key) == ("pk-lf-test", "sk-lf-test")
    assert telemetry.clients == []


def test_explicit_arguments_win_over_the_environment(telemetry, langfuse_keys):
    langfuse_keys()

    settings = Langfuse(public_key="pk-code", base_url="https://langfuse.example/").build()

    assert (settings.public_key, settings.secret_key, settings.base_url) == (
        "pk-code",
        "sk-lf-test",
        "https://langfuse.example/",
    )


def test_declaring_observability_with_no_keys_anywhere_is_refused(monkeypatch):
    monkeypatch.setenv("AGENTDECK_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("AGENTDECK_LANGFUSE_SECRET_KEY", "")
    reset_settings_cache()
    try:
        with pytest.raises(ConfigError, match="no Langfuse keys are configured"):
            Langfuse().build()
    finally:
        reset_settings_cache()


def test_a_handed_in_client_cannot_also_be_reconfigured():
    with pytest.raises(ConfigError, match="cannot be reconfigured"):
        Langfuse(client=SpyClient(), base_url="https://langfuse.example/")


def test_a_handed_in_client_needs_no_keys_at_all(monkeypatch):
    monkeypatch.setenv("AGENTDECK_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("AGENTDECK_LANGFUSE_SECRET_KEY", "")
    reset_settings_cache()
    try:
        assert Langfuse(client=SpyClient()).build().enabled is False
    finally:
        reset_settings_cache()


async def test_deck_build_with_observability_configured_opens_no_socket(project, telemetry, langfuse_keys, monkeypatch):
    """``build()`` stays a CI-runnable check with observability declared.

    Two assertions, because either alone is weak: no client was constructed, and no socket was
    opened by anything at all — the network ban is what makes ``build()`` safe to run where
    Langfuse is unreachable, and a client that quietly resolved DNS would still break that.
    """
    import socket

    from agentdeck.deck import Deck

    langfuse_keys()
    deck = Deck(observability=Langfuse())

    def _no_sockets(*_args, **_kwargs):
        raise AssertionError("build() opened a socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)
    monkeypatch.setattr(socket, "create_connection", _no_sockets)
    deck.build()

    assert telemetry.clients == []
    await deck.aclose()


def test_importing_the_capability_does_not_need_the_observability_extra():
    """``agentdeck.observability`` is imported by ``agentdeck.deck``, so it is on every entry
    point's import path — the SDK import has to stay behind ``open()``. A fresh interpreter,
    because this one has already imported half the world."""
    probe = (
        "import sys;"
        "from agentdeck.observability import Langfuse;"
        "Langfuse(public_key='pk', secret_key='sk').build();"
        "assert 'langfuse' not in sys.modules, sorted(m for m in sys.modules if 'langfuse' in m);"
        "print('built, and still no sdk')"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr
    assert "built, and still no sdk" in done.stdout


# --- the lifecycle: once, at open, before any run ---------------------------------------------


async def test_tracing_starts_once_at_open_and_never_during_a_run(project, telemetry, langfuse_keys, scripted):  # noqa: ARG001 — fixtures set the environment up
    """One client for the whole life of a deck, built when it opens.

    The count is the assertion #162 needs: two constructions was the bug, and the SDK caches
    per public key so the second one's arguments are discarded rather than applied. Runs are
    counted too, since the old ordering started tracing on the *first run* instead.
    """
    from agentdeck.deck import Deck

    langfuse_keys()
    deck = Deck.from_project(observability=Langfuse())

    deck.build()
    assert telemetry.clients == []

    async with deck:
        assert len(telemetry.clients) == 1
        await deck.run("Greeter", "hello", session_id="s-1")
        await deck.run("Greeter", "again", session_id="s-1")
        assert len(telemetry.clients) == 1


async def test_an_open_deck_traces_its_runs_under_their_own_session(project, telemetry, langfuse_keys, scripted):  # noqa: ARG001 — fixtures set the environment up
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project(observability=Langfuse()) as deck:
        result = await deck.run("Greeter", "hello", session_id="wa-123")

    assert result.output == "hi"
    [trace] = telemetry.roots
    assert (trace.name, trace.kind, trace.session_id) == ("Greeter", "agent", "wa-123")


async def test_a_deck_without_observability_traces_nothing_and_says_nothing(
    project,
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — fixtures set the environment up
    caplog,
):
    """Tracing off is the quiet default: no client, no trace, and nothing warned about it.

    The keys are set on purpose. Omitting ``observability=`` from a code-first ``Deck`` is the
    declaration that this deck does not trace, and settings must not talk it back on.
    """
    from agentdeck.deck import Deck

    langfuse_keys()

    with caplog.at_level(logging.WARNING, logger="agentdeck"):
        async with Deck(agents=[_greeter()]) as deck:
            await deck.run("Greeter", "hello")

    assert telemetry.clients == []
    assert telemetry.roots == []
    assert [record.message for record in caplog.records if "langfuse" in record.message.lower()] == []


async def test_from_project_takes_the_environment_as_the_declaration(project, telemetry, langfuse_keys, scripted):  # noqa: ARG001 — fixtures set the environment up
    """The directory front door has no code to declare it in, and ``agentdeck serve`` is that
    front door — so for ``from_project`` the configured keys are what asks for tracing."""
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project() as deck:
        await deck.run("Greeter", "hello", session_id="s-9")

    assert len(telemetry.clients) == 1
    assert [(root.name, root.session_id) for root in telemetry.roots] == [("Greeter", "s-9")]


async def test_from_project_without_keys_stays_untraced(project, telemetry, scripted, monkeypatch):  # noqa: ARG001 — fixtures set the environment up
    from agentdeck.deck import Deck

    monkeypatch.setenv("AGENTDECK_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("AGENTDECK_LANGFUSE_SECRET_KEY", "")
    reset_settings_cache()
    try:
        async with Deck.from_project() as deck:
            await deck.run("Greeter", "hello")
    finally:
        reset_settings_cache()

    assert telemetry.clients == []
    assert telemetry.roots == []


# --- ownership -------------------------------------------------------------------------------


async def test_the_deck_shuts_down_a_client_it_constructed(project, telemetry, langfuse_keys):
    from agentdeck.deck import Deck

    langfuse_keys()

    async with Deck.from_project(observability=Langfuse()):
        pass

    [client] = telemetry.clients
    assert client.shutdowns == 1


async def test_the_deck_never_shuts_down_a_client_handed_in(project, telemetry, langfuse_keys):
    from agentdeck.deck import Deck

    langfuse_keys()
    mine = SpyClient()

    async with Deck.from_project(observability=Langfuse(client=mine)):
        pass

    assert mine.shutdowns == 0
    assert telemetry.clients == [], "a client was handed in; the deck must not build a second one"


# --- #162's second defect: no orphan trees ------------------------------------------------------


async def test_a_workflow_turn_driving_an_agent_exports_exactly_one_trace_root(
    project,
    telemetry,
    langfuse_keys,
    scripted,  # noqa: ARG001 — fixtures set the environment up
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

    async with Deck.from_project(observability=Langfuse()) as deck:
        await deck.run("ChatFlow", {"input": "hello"}, session_id="s-7")

    assert [(root.name, root.kind, root.session_id) for root in telemetry.roots] == [("ChatFlow", "chain", "s-7")]
    assert "openinference.instrumentation.openai_agents" not in sys.modules


def test_nothing_in_the_package_instruments_the_agents_sdk():
    """The other half of the orphan fix, pinned by name rather than by count.

    ``OpenAIAgentsInstrumentor().instrument()`` is what put OpenInference spans on the global
    tracer provider with no root to hang off. Nothing calls it any more; a caller that came
    back would restore the second tree without failing the count test above, which only covers
    the paths it exercises.
    """
    found = subprocess.run(
        ["git", "grep", "-l", "OpenAIAgentsInstrumentor", "--", "agentdeck/"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert found.stdout == "", f"the Agents-SDK instrumentor is back in {found.stdout.split()}"


# --- the one construction point ----------------------------------------------------------------


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
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", raising=False)
    constructed: dict[str, Any] = {}

    class _FakeSdk:
        Langfuse = staticmethod(lambda **kwargs: constructed.update(kwargs) or object())

    monkeypatch.setitem(sys.modules, "langfuse", _FakeSdk)
    import os

    langfuse_client.build_client(
        LangfuseSettings(public_key="pk", secret_key="sk", service_name="booking-svc", environment="prod")
    )

    assert os.environ["OTEL_SERVICE_NAME"] == "booking-svc"
    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_TIMEOUT"] == "5"
    assert (constructed["public_key"], constructed["environment"]) == ("pk", "prod")


def _greeter():
    from agentdeck.authoring import Agent

    return Agent(name="Greeter", instructions="Greet the user.")
