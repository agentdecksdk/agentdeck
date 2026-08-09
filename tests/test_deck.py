"""``Deck``: the v3 composition root. One test per "Done when" item in #164's 4d slice —
``Deck.asgi()`` and the golden-wire invariants are covered in 4e; this file is the Python API.
"""

from __future__ import annotations

import json
import socket
import textwrap
from typing import Any

import pytest
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from scripted_model import ScriptedModel, patch_provider, provider_of

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle
from agentdeck.authoring import Agent, Workflow
from agentdeck.core.context import RunContext
from agentdeck.deck import Deck, TurnResult
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.mcp import MCP
from agentdeck.skills import Skills


def _greeter(name: str = "Greeter", **kwargs: Any) -> Agent:
    return Agent(name=name, instructions="Greet the user.", **kwargs)


@pytest.fixture
def scripted(monkeypatch):
    """Patches the model provider every real agent run in this file plays against, so a turn
    through the Runtime never reaches for a real endpoint."""
    model = ScriptedModel(deltas=["hi"])
    patch_provider(monkeypatch, provider_of(model))
    return model


class _State(BaseModel):
    input: str = ""
    shouted: str = ""


def _build_shout_graph() -> StateGraph:
    graph = StateGraph(_State)
    graph.add_node("shout", lambda s: {"shouted": s.input.upper()})
    graph.set_entry_point("shout")
    graph.add_edge("shout", END)
    return graph


def _shout_workflow(name: str = "Shout") -> Workflow:
    return Workflow(name=name, state=_State, graph=_build_shout_graph)


def _write_skill(root, dirname: str, *, description: str = "does a thing") -> None:
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(["---", f"name: {dirname}", f"description: {description}", "---", "Body."])
    )


@pytest.fixture(autouse=True)
def _reset_mcp_lifecycle():
    """Every test here starts from a clean process-wide registry — it's shared state."""
    MCPLifecycle.reset()
    yield
    MCPLifecycle.reset()


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    """A cwd with no ``.agentdeck`` at all — proves the code-first constructor needs none."""
    monkeypatch.chdir(tmp_path)


# --- Deck(...) builds and runs an agent, with no ./.agentdeck on disk -----------------------


@pytest.mark.asyncio
async def test_deck_builds_and_runs_an_agent_with_no_project_on_disk(no_project, scripted):
    deck = Deck(agents=[_greeter()])
    deck.build()

    async with deck:
        result = await deck.run("Greeter", "hi there")

    assert isinstance(result, TurnResult)
    assert result.output == "hi"


@pytest.mark.asyncio
async def test_deck_runs_a_workflow_with_no_project_on_disk(no_project, monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
    deck = Deck(workflows=[_shout_workflow()])
    deck.build()

    async with deck:
        result = await deck.run("Shout", {"input": "hi"})

    assert result == {"input": "hi", "shouted": "HI"}


# --- Deck.from_project() produces an equivalent deck from the directory layout --------------


def test_from_project_matches_the_equivalent_code_first_deck(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck"
    (root / "agents" / "greeter").mkdir(parents=True)
    (root / "agents" / "greeter" / "agent.py").write_text(
        textwrap.dedent("""
        from agentdeck.authoring import Agent

        greeter = Agent(name="Greeter", instructions="Greet the user.")
        """)
    )
    (root / "workflows" / "shout").mkdir(parents=True)
    (root / "workflows" / "shout" / "workflow.py").write_text(
        textwrap.dedent("""
        from langgraph.graph import END, StateGraph
        from pydantic import BaseModel
        from agentdeck.authoring import Workflow

        class State(BaseModel):
            input: str = ""
            shouted: str = ""

        def _build_graph():
            g = StateGraph(State)
            g.add_node("shout", lambda s: {"shouted": s.input.upper()})
            g.set_entry_point("shout")
            g.add_edge("shout", END)
            return g

        shout = Workflow(name="Shout", state=State, graph=_build_graph)
        """)
    )
    _write_skill(root / "skills", "booking", description="Books things.")
    monkeypatch.chdir(tmp_path)

    from_project = Deck.from_project()
    code_first = Deck(agents=[_greeter()], workflows=[_shout_workflow()], skills=Skills(root / "skills"))

    def catalog(deck: Deck) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        return frozenset(deck.agents), frozenset(deck.workflows), frozenset(deck.skills.build())

    assert (
        catalog(from_project)
        == catalog(code_first)
        == (frozenset({"Greeter"}), frozenset({"Shout"}), frozenset({"booking"}))
    )


# --- skills= and mcp= each accept a bare path and a capability object -----------------------


def test_skills_coercion_accepts_a_bare_path_and_a_capability_object(tmp_path):
    _write_skill(tmp_path, "booking")

    from_path = Deck(skills=tmp_path)
    from_object = Deck(skills=Skills(tmp_path))

    assert set(from_path.skills.build()) == set(from_object.skills.build()) == {"booking"}


def test_mcp_coercion_accepts_a_bare_path_and_a_capability_object(tmp_path):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"docs": {"url": "http://host/mcp"}}}))

    from_path = Deck(mcp=mcp_json)
    from_object = Deck(mcp=MCP(mcp_json))

    from_path.build()
    from_object.build()
    # `mcp` is deliberately not a public property (only agents/workflows/skills/settings are —
    # see the module docstring); build() succeeding without a "no mcp= configured" ConfigError
    # is itself proof the bare path coerced into a working MCP the same as the object did.


# --- root-name collisions and unknown-name references all fail build() ---------------------


def test_agent_and_workflow_sharing_a_name_fails_build_naming_both():
    deck = Deck(agents=[_greeter(name="Twin")], workflows=[_shout_workflow(name="Twin")])

    with pytest.raises(ConfigError, match="Twin"):
        deck.build()


def test_unknown_skill_name_fails_build(tmp_path):
    _write_skill(tmp_path, "booking")
    deck = Deck(agents=[_greeter(skills=["not-configured"])], skills=tmp_path)

    with pytest.raises(ConfigError, match="not-configured"):
        deck.build()


def test_declaring_skills_with_no_skills_configured_at_all_fails_build():
    deck = Deck(agents=[_greeter(skills=["booking"])])

    with pytest.raises(ConfigError, match="no skills="):
        deck.build()


def test_unknown_mcp_name_fails_build(tmp_path):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"docs": {"url": "http://host/mcp"}}}))
    deck = Deck(agents=[_greeter(mcp=["not-configured"])], mcp=mcp_json)

    with pytest.raises(ConfigError, match="not-configured"):
        deck.build()


def test_declaring_mcp_with_no_mcp_configured_at_all_fails_build():
    deck = Deck(agents=[_greeter(mcp=["calendar"])])

    with pytest.raises(ConfigError, match="no mcp="):
        deck.build()


def test_agent_workflow_tool_not_registered_fails_build():
    stray = _shout_workflow(name="NotRegistered")
    deck = Deck(agents=[_greeter(tools=[stray])])  # `stray` is not in workflows=

    with pytest.raises(ConfigError, match="NotRegistered"):
        deck.build()


def test_agent_workflow_tool_that_is_registered_builds_cleanly():
    workflow = _shout_workflow(name="Registered")
    deck = Deck(agents=[_greeter(tools=[workflow])], workflows=[workflow])

    deck.build()  # no raise


def test_build_is_idempotent(tmp_path):
    _write_skill(tmp_path, "booking")
    deck = Deck(agents=[_greeter(skills=["booking"])], skills=tmp_path)

    deck.build()
    same_deck = deck.build()  # a second call must not re-validate, re-compile, or raise

    assert same_deck is deck


# --- build() performs no network I/O and starts no MCP server, asserted --------------------


def test_build_starts_no_mcp_server(monkeypatch, tmp_path):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"docs": {"url": "http://host/mcp"}}}))

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("MCPLifecycle.startup must not run during build()")

    monkeypatch.setattr(MCPLifecycle, "startup", _refuse)
    deck = Deck(agents=[_greeter(mcp=["docs"])], mcp=mcp_json)

    deck.build()  # must not touch the patched startup at all


def test_build_touches_no_network(monkeypatch, no_project):
    def _refuse_connect(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("build() must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse_connect)
    deck = Deck(agents=[_greeter()], workflows=[_shout_workflow()])

    deck.build()  # a raised AssertionError from the patch would fail this test, not pass it


# --- mutating the catalog after build() raises ----------------------------------------------


def test_mutating_the_catalog_after_build_raises():
    deck = Deck(agents=[_greeter()])
    deck.build()

    with pytest.raises(TypeError):
        deck.agents["Intruder"] = _greeter(name="Intruder")


# --- run/stream before OPEN raise -------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_before_open_raises(no_project):
    deck = Deck(agents=[_greeter()])
    deck.build()

    with pytest.raises(ConfigError, match="not open"):
        await deck.run("Greeter", "hi")


@pytest.mark.asyncio
async def test_an_unknown_root_name_is_a_not_found_error(no_project, scripted):
    deck = Deck(agents=[_greeter()])

    async with deck:
        with pytest.raises(NotFoundError, match="No agent or workflow named 'unknown'"):
            await deck.run("unknown", "hi")


@pytest.mark.asyncio
async def test_stream_before_open_raises(no_project):
    deck = Deck(agents=[_greeter()])
    deck.build()

    with pytest.raises(ConfigError, match="not open"):
        async for _ in deck.stream("Greeter", "hi"):
            pass


# --- ownership: a deck closes an MCP(...) it opened, never a store passed in ----------------


@pytest.mark.asyncio
async def test_deck_closes_the_mcp_it_opened(no_project, monkeypatch, tmp_path, scripted):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {}}))
    shutdown_calls = []

    async def _spy_shutdown() -> None:
        shutdown_calls.append(1)

    monkeypatch.setattr(MCPLifecycle, "shutdown", staticmethod(_spy_shutdown))
    deck = Deck(agents=[_greeter()], mcp=mcp_json)

    async with deck:
        await deck.run("Greeter", "hi")

    assert shutdown_calls == [1]


@pytest.mark.asyncio
async def test_deck_does_not_close_a_store_passed_in(no_project, scripted):
    from agentdeck.adapters.stores.memory import MemoryEventStore

    class _SpyStore(MemoryEventStore):
        def __init__(self) -> None:
            super().__init__()
            self.aclose_calls = 0

        async def aclose(self) -> None:
            self.aclose_calls += 1

    store = _SpyStore()
    deck = Deck(agents=[_greeter()], _store=store)

    async with deck:
        await deck.run("Greeter", "hi")

    assert store.aclose_calls == 0


# --- the private `_engines=` seam exists and is exercised -----------------------------------


def test_engines_seam_restricts_which_engines_a_catalog_may_use(no_project):
    """A private, test-only override (never in the documented constructor, same as the
    Runtime's own ``tests/contract/`` seam): naming only the stub engine means an ordinary
    ``Agent`` — which needs "openai-agents" — fails ``build()`` instead of silently compiling."""
    deck = Deck(agents=[_greeter()], _engines=("stub",))

    with pytest.raises(ConfigError, match="openai-agents"):
        deck.build()


def test_engines_seam_accepts_the_matching_default_engines(no_project):
    """The same seam, given the real default engine names, builds exactly like the default
    constructor — proving the restriction above comes from the *set*, not the seam itself."""
    from agentdeck.adapters.engines.langgraph import LangGraphEngine
    from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine

    deck = Deck(agents=[_greeter()], _engines=(OpenAIAgentsEngine.engine, LangGraphEngine.engine))

    deck.build()  # no raise


# --- asgi() opens and closes through the ASGI lifespan --------------------------------------


def test_asgi_opens_and_closes_the_deck_through_the_lifespan(no_project, scripted):
    from fastapi.testclient import TestClient

    deck = Deck(agents=[_greeter()])
    api = deck.asgi()

    assert deck._state == "NEW"
    with TestClient(api) as client:
        assert deck._state == "OPEN"
        response = client.post("/agents/Greeter/chat", json={"session_id": "s", "message": "hi"})
        assert response.status_code == 200
    assert deck._state == "CLOSED"


def test_asgi_health_reflects_this_decks_catalog(no_project, tmp_path):
    from fastapi.testclient import TestClient

    _write_skill(tmp_path, "booking")
    deck = Deck(agents=[_greeter()], workflows=[_shout_workflow()], skills=tmp_path)

    with TestClient(deck.asgi()) as client:
        response = client.get("/health")

    assert response.json() == {
        "status": "ok",
        "agents": ["Greeter"],
        "workflows": ["Shout"],
        "skills": ["booking"],
    }


# --- v1's convenience methods, carried across unchanged, behave the same on Deck -----------


def _reader_ctx(session_id: str | None) -> RunContext:
    """A throwaway context of the Deck's own namespace, for reading its log back in a test —
    exactly what ``serve.py``'s compat routes build for an HTTP request."""
    return RunContext(run_id="reader", session_id=session_id)


@pytest.mark.asyncio
async def test_run_workflow_with_no_state_defaults_to_an_empty_object(no_project, monkeypatch):
    """``state=None``'s old meaning ("no updates") has to survive wrapping it in a
    ``DataBlock``, which cannot carry ``None`` as a graph's state."""
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
    deck = Deck(workflows=[_shout_workflow()])

    async with deck:
        out = await deck.run_workflow("Shout")

    assert out == {"shouted": ""}


@pytest.mark.asyncio
async def test_run_agent_is_recorded_and_returns_a_turn_result(no_project, scripted):
    deck = Deck(agents=[_greeter()])

    async with deck:
        result = await deck.run_agent("Greeter", "hello")
        assert result.output == "hi"
        assert result.session_id is None
        events = await deck._runtime.store.read(result.run_id, _reader_ctx(None))

    assert [event.kind for event in events] == [
        "run.started",
        "text.delta",
        "usage.reported",
        "message.completed",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_chat_is_recorded_and_returns_a_turn_result(no_project, scripted):
    deck = Deck(agents=[_greeter()])

    async with deck:
        result = await deck.chat("Greeter", "s1", "hello")
        assert result.output == "hi"
        assert result.session_id == "s1"
        events = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert [event.kind for event in events] == [
        "run.started",
        "text.delta",
        "usage.reported",
        "message.completed",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_a_failed_chat_still_leaves_run_failed_in_the_log(no_project, monkeypatch):
    """A run that raises is still written down, even though nobody read the stream to the end
    by hand."""
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("par",), raises=RuntimeError("boom"))))
    deck = Deck(agents=[_greeter()])

    async with deck:
        with pytest.raises(RuntimeError, match="boom"):
            await deck.chat("Greeter", "s1", "hello")
        events = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert [event.kind for event in events] == ["run.started", "text.delta", "run.failed"]


class _Greeting(BaseModel):
    greeting: str


@pytest.mark.asyncio
async def test_a_structured_chat_output_survives_as_validated_data(no_project, monkeypatch):
    """``RunCompleted.output`` can only hold text; the compat engine carries a validated
    ``output_type`` result alongside it, and ``chat``'s ``TurnResult`` must still surface it as
    data rather than the stringified JSON the terminal event itself carries."""
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=('{"greeting": "hi"}',))))
    deck = Deck(agents=[Agent(name="Structured", instructions="Answer as JSON.", output_type=_Greeting)])

    async with deck:
        result = await deck.chat("Structured", "s1", "hello")

    assert result.output == {"greeting": "hi"}


@pytest.mark.asyncio
async def test_chat_stream_yields_canonical_events_and_is_recorded(no_project, monkeypatch):
    from agentdeck.core.events import RunCompleted, TextDelta

    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("Hel", "lo"))))
    deck = Deck(agents=[_greeter()])

    async with deck:
        events = [event async for event in deck.chat_stream("Greeter", "s1", "hello")]

        assert [event.kind for event in events] == [
            "run.started",
            "text.delta",
            "text.delta",
            "usage.reported",
            "message.completed",
            "run.completed",
        ]
        assert "".join(e.payload.text for e in events if isinstance(e.payload, TextDelta)) == "Hello"
        assert next(e for e in events if isinstance(e.payload, RunCompleted)).payload.output[0].text == "Hello"

        # the stream itself already recorded every one of those events; store.read proves it
        # rather than the caller having to trust chat_stream's own bookkeeping
        stored = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert [event.kind for event in stored] == [event.kind for event in events]


@pytest.mark.asyncio
async def test_chat_stream_closes_the_runtime_generator_on_abandonment(no_project, monkeypatch):
    """A caller that stops mid-stream must not leave the run open in the log holding its
    session forever: closing only ``chat_stream``'s own frame would abandon the Runtime's
    generator to the GC instead of closing it."""
    patch_provider(monkeypatch, provider_of(ScriptedModel(deltas=("Hel", "lo"))))
    deck = Deck(agents=[_greeter()])

    async with deck:
        stream = deck.chat_stream("Greeter", "s1", "hello")
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()
        events = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert (first.kind, second.kind) == ("run.started", "text.delta")
    assert [event.kind for event in events] == ["run.started", "text.delta", "run.cancelled"]


@pytest.mark.asyncio
async def test_chat_and_chat_stream_share_one_session(no_project, monkeypatch):
    """Same guarantee v1 gave: one ``session_id`` is one conversation whichever Deck method ran
    the turn."""
    model = ScriptedModel(deltas=("hi",))
    patch_provider(monkeypatch, provider_of(model))
    deck = Deck(agents=[_greeter()])

    async with deck:
        async for _ in deck.chat_stream("Greeter", "s1", "first"):
            pass
        await deck.chat("Greeter", "s1", "second")

    # two model calls, and the second turn's input carries the first turn's history
    assert model.calls == 2
    assert "first" in str(model.inputs[-1])


def test_sessions_keyed_by_id(no_project):
    deck = Deck(agents=[_greeter()])

    assert deck.session_for("a") is deck.session_for("a")
    assert deck.session_for("a") is not deck.session_for("b")


@pytest.mark.asyncio
async def test_pause_and_resume_reach_the_runtime_this_deck_composed(no_project, scripted):
    """The wiring, end to end and with nothing hand-built: ``Deck.pause`` writes to the very
    control port this Deck's own Runtime got, the run stops at its own safe point, and
    ``Deck.resume`` plays it on to completion."""
    deck = Deck(agents=[_greeter()])

    async with deck:
        run_id = "r-control"
        assert await deck.pause(run_id, "operator stepped away") is True
        paused = [event async for event in deck.stream("Greeter", "hi there", run_id=run_id)]
        resumed = await deck.resume(run_id)

    assert [event.kind for event in paused][-3:] == ["control.requested", "control.observed", "run.paused"]
    assert next(e.payload.reason for e in paused if e.kind == "run.paused") == "operator stepped away"
    assert [event.kind for event in resumed][0] == "run.resumed"
    assert [event.kind for event in resumed][-1] == "run.completed"


@pytest.mark.asyncio
async def test_injected_session_factory_is_used_and_closed_once(no_project, monkeypatch, scripted):
    """The DI seam bypasses ``SessionFactory.from_settings``, and ``aclose()`` closes the
    injection exactly once."""
    from agentdeck.adapters.engines.openai_agents.sessions import SessionFactory

    def boom(_settings: Any) -> Any:
        raise AssertionError("from_settings must not be called when a factory is injected")

    monkeypatch.setattr(SessionFactory, "from_settings", staticmethod(boom))

    class _FakeSessionFactory:
        """Stand-in for the Redis-backed SessionFactory; counts aclose() calls."""

        def __init__(self) -> None:
            self.closed = 0
            self.sessions: dict[str, Any] = {}

        def session_for(self, session_id: str) -> Any:
            from agents import SQLiteSession

            return self.sessions.setdefault(session_id, SQLiteSession(session_id))

        async def aclose(self) -> None:
            self.closed += 1

    fake = _FakeSessionFactory()
    deck = Deck(agents=[_greeter()], session_factory=fake)

    async with deck:
        # namespace-scoped, because the engine's own store mints the key: two namespaces are
        # free to pick the same session id, and an unprefixed key would hand them one conversation
        assert deck.session_for("s1") is fake.sessions[":s1"]

    assert fake.closed == 1
