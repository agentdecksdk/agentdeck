"""The assembly seam: one function builds every Runtime, and Deck is one of its callers."""

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import live_stores
import pytest
from project_engines import project_engines
from scripted_model import ScriptedModel, patch_provider, provider_of

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.adapters.telemetry.langfuse import client as langfuse_client
from agentdeck.composition import build_runtime, resolve_event_store
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.errors import NotFoundError
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import EventsSettings, reset_settings_cache

AGENT_PY = """
from agentdeck.authoring import Agent

greeter = Agent(name="Greeter", instructions="Greet the user.")
"""

WORKFLOW_PY = """
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agentdeck.authoring import Workflow


class State(TypedDict, total=False):
    input: str
    shouted: str


def _build_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"shouted": s["input"].upper()})
    g.set_entry_point("shout")
    g.add_edge("shout", END)
    return g


shout = Workflow(name="Shout", state=State, graph=_build_graph)
"""

CTX = RunContext(namespace="local", run_id="r1")


@dataclass
class Opened:
    """One observation the sink opened — what would have become a Langfuse span."""

    name: str
    kind: str
    session_id: str | None = None
    children: list["Opened"] = field(default_factory=list)

    def child(self, name: str, *, kind: str, input: Any = None, metadata: Any = None) -> "Opened":  # noqa: A002, ARG002 — the Tracer port's own signature
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


@pytest.fixture
def recorded_traces(monkeypatch):
    """Swap the Langfuse SDK for a recorder, so what the composition root wires is assertable
    without the ``[observability]`` extra, a key that reaches anything, or a network."""
    tracer = RecordingTracer()
    monkeypatch.setattr(langfuse_client, "_build_client", lambda _settings: None)
    monkeypatch.setattr(langfuse_client, "LangfuseTracer", lambda _client: tracer)
    return tracer


@pytest.fixture
def langfuse_keys(monkeypatch):
    """Set the two keys that decide whether Langfuse is configured at all.

    The settings cache is cleared on the way out as well as in: ``monkeypatch`` restores the
    environment but not an ``lru_cache``, and a leaked one would leave every later test in
    this process running with Langfuse on.
    """

    def _set(public_key: str, secret_key: str) -> None:
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
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(textwrap.dedent(WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    return tmp_path


def test_the_project_engine_set_covers_both_bundle_shapes():
    """Discovery refuses a project whose workflows have no engine, so both are registered."""
    engines = project_engines()
    assert sorted(engine.engine for engine in engines) == ["langgraph", "openai-agents"]
    assert [type(engine).__name__ for engine in engines] == ["OpenAIAgentsEngine", "LangGraphEngine"]


async def test_deck_wires_the_same_engines_this_suite_builds_by_hand(project):
    """What keeps ``tests/project_engines.py`` honest: the composition root is the only
    production caller, so a test set that stopped matching its wiring would be testing nothing."""
    from agentdeck.deck import Deck

    deck = Deck.from_project()

    async with deck:
        assert [type(engine).__name__ for engine in deck._runtime._engines.values()] == [
            type(engine).__name__ for engine in project_engines()
        ]


async def test_build_runtime_discovers_the_project_when_given_no_invocables(project):
    runtime = build_runtime(engines=project_engines(), store=MemoryEventStore())

    kinds = [
        event.kind
        async for event in runtime.run(
            "Shout", coerce_input("hello"), run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

    assert kinds == ["run.started", "node.updated", "run.completed"]


async def test_build_runtime_takes_explicit_specs_and_a_store_that_holds_time_still(project):
    """A caller with specs in hand skips discovery, and freezes time by handing in a store with a
    clock — which is the only seam that decides a ``ts`` now (ADR-D11). ``build_runtime``'s own
    ``clock`` keyword no longer reaches anything that stamps an event."""
    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    engines = project_engines()
    specs = InvocableRegistry(engines).load()

    runtime = build_runtime(engines=engines, invocables=specs, store=MemoryEventStore(clock=lambda: frozen))
    stamps = {
        event.ts
        async for event in runtime.run(
            "Shout", coerce_input("hello"), run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    }

    assert stamps == {frozen}


async def test_build_runtime_refuses_an_unknown_invocable(project):
    runtime = build_runtime(engines=project_engines(), store=MemoryEventStore())

    with pytest.raises(NotFoundError):
        [
            event
            async for event in runtime.run(
                "Nope",
                coerce_input("hello"),
                run_id=(CTX).run_id,
                session_id=(CTX).session_id,
                namespace=(CTX).namespace,
            )
        ]


def test_resolve_event_store_defaults_to_memory():
    assert isinstance(resolve_event_store(EventsSettings()), MemoryEventStore)


def test_resolve_event_store_builds_sqlite_from_a_scheme_url(tmp_path):
    store = resolve_event_store(EventsSettings(url=f"sqlite://{tmp_path / 'events.sqlite3'}"))

    assert isinstance(store, SqliteEventStore)
    store.close()


def test_events_url_is_settable_from_config_yaml_not_only_the_env_var(tmp_path, monkeypatch):
    """``_bare_env_names`` rewired ``EventsSettings``'s source tuple — this proves the YAML
    channel it shares with every other layered settings class still reaches the field, and
    that a real env var still outranks it, the same layering order as everything else."""
    yaml_path = tmp_path / "config.yaml"
    db_path = tmp_path / "events.sqlite3"
    yaml_path.write_text(f"events:\n  url: sqlite://{db_path}\n")
    monkeypatch.setenv("AGENTDECK_CONFIG_PATH", str(yaml_path))
    monkeypatch.delenv("AGENTDECK_EVENTS", raising=False)
    reset_settings_cache()
    try:
        store = resolve_event_store(EventsSettings())
        assert isinstance(store, SqliteEventStore)
        store.close()

        monkeypatch.setenv("AGENTDECK_EVENTS", "memory://")
        store = resolve_event_store(EventsSettings())
        assert isinstance(store, MemoryEventStore)
    finally:
        reset_settings_cache()


def test_resolve_event_store_rejects_sqlite_with_no_path_after_the_scheme():
    with pytest.raises(ValueError, match="AGENTDECK_EVENTS=sqlite"):
        resolve_event_store(EventsSettings(url="sqlite://"))


def test_resolve_event_store_rejects_an_unknown_scheme():
    with pytest.raises(ValueError, match="unknown event store scheme"):
        resolve_event_store(EventsSettings(url="not-a-backend://nothing"))


def test_resolve_event_store_builds_redis_from_a_url():
    """No server needed: the client connects lazily, so wiring is checkable without one."""
    from agentdeck.adapters.stores.redis import RedisEventStore

    store = resolve_event_store(EventsSettings(url="redis://localhost:6379/0"))

    assert isinstance(store, RedisEventStore)


def test_resolve_event_store_builds_redis_from_a_tls_url():
    """``rediss://`` is TLS Redis — the old code let any string through to ``Redis.from_url``,
    and scheme dispatch must not narrow that to plain ``redis://`` only."""
    from agentdeck.adapters.stores.redis import RedisEventStore

    store = resolve_event_store(EventsSettings(url="rediss://localhost:6380/0"))

    assert isinstance(store, RedisEventStore)


def test_resolve_event_store_builds_postgres_from_a_dsn():
    live_stores.require_psycopg()
    from agentdeck.adapters.stores.postgres import PostgresEventStore

    store = resolve_event_store(EventsSettings(url="postgresql://localhost/whatever"))

    assert isinstance(store, PostgresEventStore)


def test_a_memory_scheme_cannot_construct_a_different_stores_class(monkeypatch):
    """Issue #155's core claim, made concrete: with one variable, there is no second decision
    left to disagree with it. ``AGENTDECK_EVENTS_BACKEND``/``AGENTDECK_EVENTS_URL`` have no
    field left to bind to — so setting them alongside ``AGENTDECK_EVENTS`` cannot steer
    construction at all, let alone toward a mismatched adapter."""
    monkeypatch.setenv("AGENTDECK_EVENTS_BACKEND", "postgres")
    monkeypatch.setenv("AGENTDECK_EVENTS_URL", "redis://localhost:6379")
    monkeypatch.setenv("AGENTDECK_EVENTS", "memory://")
    reset_settings_cache()
    try:
        store = resolve_event_store()
    finally:
        reset_settings_cache()

    assert isinstance(store, MemoryEventStore)


def test_resolve_control_port_defaults_to_memory():
    from agentdeck.adapters.control.memory import MemoryControlPort
    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    assert isinstance(resolve_control_port(ControlSettings()), MemoryControlPort)


def test_resolve_control_port_builds_sqlite_from_a_scheme_url(tmp_path):
    from agentdeck.adapters.control.sqlite import SqliteControlPort
    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    port = resolve_control_port(ControlSettings(url=f"sqlite://{tmp_path / 'control.sqlite3'}"))

    assert isinstance(port, SqliteControlPort)


def test_resolve_control_port_rejects_sqlite_with_no_path_after_the_scheme():
    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    with pytest.raises(ValueError, match="AGENTDECK_CONTROL=sqlite"):
        resolve_control_port(ControlSettings(url="sqlite://"))


def test_resolve_control_port_rejects_an_unknown_scheme():
    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    with pytest.raises(ValueError, match="unknown control backend"):
        resolve_control_port(ControlSettings(url="not-a-backend://nothing"))


@pytest.mark.parametrize(
    ("url", "expected"),
    # ``memory`` ignores whatever comes back as its second element (``_memory_saver`` takes no
    # args), so it is the original url, unstripped — only ``sqlite`` strips the scheme.
    [("memory://", ("memory", "memory://")), ("sqlite://.agentdeck/x.db", ("sqlite", ".agentdeck/x.db"))],
)
def test_resolve_checkpoint_derives_backend_and_path_from_the_scheme(url, expected):
    from types import SimpleNamespace

    from agentdeck.composition import resolve_checkpoint
    from agentdeck.runtime.settings import CheckpointSettings

    settings = SimpleNamespace(checkpoint=CheckpointSettings(url=url))

    assert resolve_checkpoint(settings) == expected


def test_resolve_checkpoint_normalizes_postgresql_to_the_postgres_backend_name():
    """``resolve_checkpointer`` (the langgraph adapter) speaks ``postgres``, not the URL
    scheme's own ``postgresql`` — the composition root's job is to make that seam invisible."""
    from types import SimpleNamespace

    from agentdeck.composition import resolve_checkpoint
    from agentdeck.runtime.settings import CheckpointSettings

    settings = SimpleNamespace(checkpoint=CheckpointSettings(url="postgresql://user@host/db"))

    assert resolve_checkpoint(settings) == ("postgres", "postgresql://user@host/db")


def test_resolve_event_store_warns_when_memory_is_selected(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="agentdeck.composition"):
        resolve_event_store(EventsSettings())

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert [m for m in messages if "AGENTDECK_EVENTS" in m and "memory" in m]


def test_resolve_control_port_warns_when_memory_is_selected(caplog):
    import logging

    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    with caplog.at_level(logging.WARNING, logger="agentdeck.composition"):
        resolve_control_port(ControlSettings())

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert [m for m in messages if "AGENTDECK_CONTROL" in m and "memory" in m]


def test_a_runtime_constructs_with_no_settings_available(monkeypatch):
    """Issue #155 item 7: ``Runtime`` takes no ambient configuration at all. Settings itself is
    made to raise, so a bare ``Runtime(...)`` succeeding — with the literal one-hour default —
    is proof the constructor never reaches for it, not an inference from reading the source."""
    from datetime import timedelta

    from agentdeck.runtime import settings as settings_module

    def _boom():
        raise AssertionError("Runtime must not call get_settings() itself")

    monkeypatch.setattr(settings_module, "get_settings", _boom)

    runtime = Runtime([], MemoryEventStore(), {})

    assert runtime._stale_run_after == timedelta(hours=1)  # noqa: SLF001 — the literal default


def test_build_runtime_resolves_stale_run_after_from_settings(monkeypatch):
    """``build_runtime`` is the caller that reads ``AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS``
    and passes it to ``Runtime`` explicitly — the same as its other five arguments."""
    from datetime import timedelta

    monkeypatch.setenv("AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS", "123")
    reset_settings_cache()
    try:
        runtime = build_runtime(engines=[], invocables={}, store=MemoryEventStore(), sinks=())
    finally:
        reset_settings_cache()

    assert runtime._stale_run_after == timedelta(seconds=123)  # noqa: SLF001 — resolved explicitly by build_runtime


def test_choosing_a_store_does_not_make_the_durability_extra_mandatory():
    """``composition`` is on every entry point's import path and ``psycopg`` is an optional
    extra, so its import has to stay inside the branch that asks for it. A fresh interpreter,
    because this one has already imported half the world."""
    probe = "import agentdeck.composition, sys; print('psycopg' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True)

    assert done.stdout.strip() == "False"


async def test_a_configured_langfuse_traces_a_workflow_run_under_its_session(project, recorded_traces, langfuse_keys):
    """The gap this closes: nothing in production ever registered the sink, and the observation
    v1 opened around a workflow run was handed an identity nobody bound, so a workflow reached
    Langfuse as an anonymous trace at best. A Runtime built the ordinary way now traces the run
    from its own events — session included, node by node.
    """
    langfuse_keys("pk-lf-test", "sk-lf-test")
    runtime = build_runtime(engines=project_engines(), store=MemoryEventStore())

    async for _ in runtime.run(
        "Shout", coerce_input("hello"), run_id=CTX.run_id, session_id="s-1", namespace=CTX.namespace
    ):
        pass
    await runtime.drain()

    [trace] = recorded_traces.roots
    assert (trace.name, trace.kind, trace.session_id) == ("Shout", "chain", "s-1")
    assert trace.shape() == [("shout", "span"), ("run.usage", "generation")]


async def test_a_run_with_a_session_id_reaches_langfuse_under_its_own_session(
    project, recorded_traces, langfuse_keys, monkeypatch
):
    """The identity ``Deck.run`` already gave its trace, kept while its owner changes: the
    engine no longer opens an observation of its own, so if the sink did not carry the session
    across, every turn would go anonymous the moment the wrapping span was removed."""
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("hi",))))
    langfuse_keys("pk-lf-test", "sk-lf-test")
    from agentdeck.deck import Deck

    async with Deck.from_project() as deck:
        result = await deck.run("Greeter", "hello", session_id="wa-123")

    assert result.output == "hi"
    [trace] = recorded_traces.roots
    assert (trace.name, trace.kind, trace.session_id) == ("Greeter", "agent", "wa-123")


async def test_an_unconfigured_langfuse_leaves_the_run_untraced(project, recorded_traces, langfuse_keys):
    """Without keys there is no sink in the list at all, so a run never reaches this adapter —
    the same silence v1 kept, and what makes the wiring safe to do unconditionally."""
    langfuse_keys("", "")
    runtime = build_runtime(engines=project_engines(), store=MemoryEventStore())

    async for _ in runtime.run(
        "Shout", coerce_input("hello"), run_id=(CTX).run_id, session_id=(CTX).session_id, namespace=(CTX).namespace
    ):
        pass
    await runtime.drain()

    assert recorded_traces.roots == []


def test_wiring_telemetry_does_not_make_the_observability_extra_mandatory():
    """Every ``build_runtime`` call consults Langfuse now, so the SDK import has to stay behind
    the keys. A fresh interpreter with the keys explicitly cleared, because this one has
    already imported half the world and a developer's own keys would mask the answer."""
    probe = (
        "import sys;"
        "from agentdeck.adapters.engines.stub import StubEngine;"
        "from agentdeck.composition import build_runtime;"
        "build_runtime(engines=[StubEngine()], invocables={});"
        "assert 'langfuse' not in sys.modules, sorted(m for m in sys.modules if 'langfuse' in m);"
        "print('no keys, no sdk')"
    )
    blank = {"AGENTDECK_LANGFUSE_PUBLIC_KEY": "", "AGENTDECK_LANGFUSE_SECRET_KEY": ""}
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, env={**os.environ, **blank}
    )

    assert done.returncode == 0, done.stderr
    assert "no keys, no sdk" in done.stdout


async def test_deck_composes_one_runtime_over_the_whole_project(project):
    """``Deck`` is a caller of the seam, not a second assembly: its Runtime covers every
    discovered bundle, workflows included. (``Deck()`` before ``OPEN`` refusing a run at all
    is covered directly in ``tests/test_deck.py``, which has no ``App``-shaped ``.runtime``
    property to reach into.)
    """
    from agentdeck.deck import Deck

    deck = Deck.from_project()

    async with deck:
        assert isinstance(deck._runtime, Runtime)
        kinds = [
            event.kind
            async for event in deck._runtime.run(
                "Shout",
                coerce_input("hello"),
                run_id=(CTX).run_id,
                session_id=(CTX).session_id,
                namespace=(CTX).namespace,
            )
        ]
    assert kinds == ["run.started", "node.updated", "run.completed"]
    await deck.aclose()  # idempotent, with a Runtime already drained


def test_v1s_bundle_harness_is_gone_with_no_facade():
    """``agents/``, ``workflows/`` and ``app.py`` are deleted outright — a re-export shim
    would pass this the same way a real deletion does, so it checks the module is gone from
    the package rather than merely absent from any one import."""
    import importlib.util

    assert importlib.util.find_spec("agentdeck.app") is None
    assert importlib.util.find_spec("agentdeck.agents") is None
    assert importlib.util.find_spec("agentdeck.workflows") is None
