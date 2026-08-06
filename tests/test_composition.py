"""The assembly seam: one function builds every Runtime, and App is one of its callers."""

import subprocess
import sys
import textwrap
from datetime import UTC, datetime

import pytest

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.composition import build_runtime, resolve_event_store, v1_engines
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import EventsSettings
from agentdeck.v1bridge import V1CompatEngine

AGENT_PY = """
from agentdeck.agents import BaseAgent

class Greeter(BaseAgent):
    instructions = "Greet the user."
"""

WORKFLOW_PY = """
from typing import TypedDict

from agentdeck.workflows import END, BaseWorkflow, StateGraph


class State(TypedDict, total=False):
    input: str
    shouted: str


class Shout(BaseWorkflow):
    state = State

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"shouted": s["input"].upper()})
        g.set_entry_point("shout")
        g.add_edge("shout", END)
        return g
"""

CTX = RunContext(tenant="local", principal="user:local", run_id="r1", trace_id="t1")


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


def test_v1_engines_covers_both_bundle_shapes():
    """Discovery refuses a project whose workflows have no engine, so both are registered."""
    engines = v1_engines()
    assert sorted(engine.engine for engine in engines) == ["langgraph", "openai-agents"]
    assert [type(engine) for engine in engines if engine.engine == "openai-agents"] == [V1CompatEngine]


async def test_build_runtime_discovers_the_project_when_given_no_invocables(project):
    runtime = build_runtime(engines=v1_engines(), store=MemoryEventStore())

    kinds = [event.kind async for event in runtime.run("Shout", coerce_input("hello"), CTX)]

    assert kinds == ["run.started", "node.updated", "run.completed"]


async def test_build_runtime_takes_explicit_specs_and_a_clock(project):
    """A caller with specs in hand skips discovery, and injects a clock instead of waiting
    for wall time to be deterministic."""
    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    engines = v1_engines()
    specs = InvocableRegistry(engines).load()

    runtime = build_runtime(engines=engines, invocables=specs, store=MemoryEventStore(), clock=lambda: frozen)
    stamps = {event.ts async for event in runtime.run("Shout", coerce_input("hello"), CTX)}

    assert stamps == {frozen}


async def test_build_runtime_refuses_an_unknown_invocable(project):
    runtime = build_runtime(engines=v1_engines(), store=MemoryEventStore())

    with pytest.raises(NotFoundError):
        [event async for event in runtime.run("Nope", coerce_input("hello"), CTX)]


def test_resolve_event_store_defaults_to_memory():
    assert isinstance(resolve_event_store(EventsSettings(backend="memory")), MemoryEventStore)


def test_resolve_event_store_builds_sqlite_from_a_path(tmp_path):
    store = resolve_event_store(EventsSettings(backend="sqlite", url=str(tmp_path / "events.sqlite3")))

    assert isinstance(store, SqliteEventStore)
    store.close()


def test_resolve_event_store_rejects_sqlite_without_a_path():
    with pytest.raises(ValueError, match="AGENTDECK_EVENTS_URL"):
        resolve_event_store(EventsSettings(backend="sqlite"))


def test_resolve_event_store_rejects_an_unknown_backend():
    with pytest.raises(ValueError, match="unknown event store backend"):
        resolve_event_store(EventsSettings(backend="not-a-backend"))


def test_resolve_event_store_builds_redis_from_a_url():
    """No server needed: the client connects lazily, so wiring is checkable without one."""
    from agentdeck.adapters.stores.redis import RedisEventStore

    store = resolve_event_store(EventsSettings(backend="redis", url="redis://localhost:6379/0"))

    assert isinstance(store, RedisEventStore)


def test_resolve_event_store_builds_postgres_from_a_dsn():
    pytest.importorskip("psycopg", reason="the Postgres event log needs the [durability] extra")
    from agentdeck.adapters.stores.postgres import PostgresEventStore

    store = resolve_event_store(EventsSettings(backend="postgres", url="postgresql://localhost/whatever"))

    assert isinstance(store, PostgresEventStore)


@pytest.mark.parametrize("backend", ["redis", "postgres"])
def test_resolve_event_store_rejects_a_shared_backend_without_a_url(backend):
    with pytest.raises(ValueError, match="AGENTDECK_EVENTS_URL"):
        resolve_event_store(EventsSettings(backend=backend))


def test_choosing_a_store_does_not_make_the_durability_extra_mandatory():
    """``composition`` is on every entry point's import path and ``psycopg`` is an optional
    extra, so its import has to stay inside the branch that asks for it. A fresh interpreter,
    because this one has already imported half the world."""
    probe = "import agentdeck.composition, sys; print('psycopg' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=True)

    assert done.stdout.strip() == "False"


def test_app_has_no_runtime_before_load(project):
    from agentdeck import App

    with pytest.raises(ConfigError, match="call App.load()"):
        _ = App().runtime


async def test_app_composes_one_runtime_over_the_whole_project(project):
    """``App`` is a caller of the seam, not a second assembly: its Runtime covers every
    discovered bundle, workflows included."""
    from agentdeck import App

    app = App()
    app.load()

    assert isinstance(app.runtime, Runtime)
    kinds = [event.kind async for event in app.runtime.run("Shout", coerce_input("hello"), CTX)]
    assert kinds == ["run.started", "node.updated", "run.completed"]
    await app.aclose()
    await app.aclose()  # idempotent, with a Runtime to drain
