"""The assembly seam: one function builds every Runtime, and Deck is one of its callers."""

import logging
import subprocess
import sys
import textwrap
from datetime import UTC, datetime

import live_stores
import pytest
from project_engines import project_engines

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.composition import build_runtime, resolve_event_store
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.errors import DOCS_URL, NotFoundError
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
            "Shout", coerce_input("hello"), session_id=(CTX).session_id, namespace=(CTX).namespace
        )
    ]

    assert kinds == ["run.started", "node.updated", "run.completed"]


async def test_build_runtime_takes_explicit_specs_and_a_store_that_holds_time_still(project):
    """A caller with specs in hand skips discovery, and freezes time by handing in a store with a
    clock — the only seam that decides a ``ts`` now (ADR-D11); ``build_runtime`` has no ``clock``
    keyword of its own."""
    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    engines = project_engines()
    specs = InvocableRegistry(engines).load()

    runtime = build_runtime(engines=engines, invocables=specs, store=MemoryEventStore(clock=lambda: frozen))
    stamps = {
        event.ts
        async for event in runtime.run(
            "Shout", coerce_input("hello"), session_id=(CTX).session_id, namespace=(CTX).namespace
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
    with pytest.raises(ValueError, match="unknown event store scheme") as excinfo:
        resolve_event_store(EventsSettings(url="not-a-backend://nothing"))

    assert f"{DOCS_URL}/concepts/choosing-a-store-backend" in str(excinfo.value)


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


def test_resolve_event_store_redis_without_the_extra_names_the_install_command():
    """#274: the redis client moved out of base into its own extra, and ``resolve_event_store``'s
    redis branch previously had no missing-extra guard at all (unlike its postgres sibling below)
    — selecting a ``redis://`` event log without the extra must fail with an agentdeck error
    naming the install command, not a raw ``ModuleNotFoundError``.

    A fresh subprocess with ``sys.modules["redis"] = None`` set before any import, because this
    process already has redis installed and imported (the tests above need it) and `sys.modules`
    cannot unsee that — same rationale as `test_openai_agents_sessions.py`'s identical probe for
    the session side of this same fix.
    """
    probe = textwrap.dedent(
        """
        import sys
        sys.modules["redis"] = None
        from agentdeck.composition import resolve_event_store
        from agentdeck.runtime.settings import EventsSettings
        try:
            resolve_event_store(EventsSettings(url="redis://localhost:6379/0"))
        except ImportError as exc:
            assert "redis" in str(exc)
            assert 'pip install "agentdeck-sdk[redis]"' in str(exc), str(exc)
            assert "choosing-a-store-backend" in str(exc), str(exc)
            print("raised the right error")
        else:
            raise AssertionError("expected an ImportError")
        """
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, done.stderr
    assert "raised the right error" in done.stdout


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


def test_the_lease_port_follows_the_control_setting(tmp_path):
    """One setting, two ports: both are ephemeral per-run state for one deployment, so an
    operator names the backend once and the lease port lands wherever the signals did."""
    from agentdeck.adapters.leases.memory import MemoryLeasePort
    from agentdeck.adapters.leases.sqlite import SqliteLeasePort
    from agentdeck.composition import resolve_lease_port
    from agentdeck.runtime.settings import ControlSettings

    assert isinstance(resolve_lease_port(ControlSettings()), MemoryLeasePort)
    assert isinstance(
        resolve_lease_port(ControlSettings(url=f"sqlite://{tmp_path / 'control.sqlite3'}")), SqliteLeasePort
    )


def test_a_memory_lease_backend_warns_that_a_killed_worker_still_wedges_its_session(caplog):
    """The default reports no knowledge about any peer, so the hour-long window is still what a
    crash costs. An operator finds that out at boot rather than during the outage."""
    from agentdeck.composition import resolve_lease_port
    from agentdeck.runtime.settings import ControlSettings

    with caplog.at_level(logging.WARNING):
        resolve_lease_port(ControlSettings())

    assert "AGENTDECK_CONTROL=sqlite" in caplog.text
    assert "killed outright" in caplog.text


def test_resolve_control_port_rejects_an_unknown_scheme():
    from agentdeck.composition import resolve_control_port
    from agentdeck.runtime.settings import ControlSettings

    with pytest.raises(ValueError, match="unknown control backend") as excinfo:
        resolve_control_port(ControlSettings(url="not-a-backend://nothing"))

    assert f"{DOCS_URL}/concepts/choosing-a-store-backend" in str(excinfo.value)


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
