"""``Deck``: the v3 composition root. One test per "Done when" item in #164's 4d slice —
``Deck.asgi()`` and the golden-wire invariants are covered in 4e; this file is the Python API.
"""

from __future__ import annotations

import importlib
import json
import socket
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from agents import WebSearchTool, function_tool
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle
from agentdeck.authoring import Agent, Workflow
from agentdeck.authoring.timers import sleep_until
from agentdeck.core.context import RunContext
from agentdeck.deck import Deck, TurnResult
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.mcp import MCP
from agentdeck.skills import Skills
from agentdeck.testing import ScriptedModel, patch_model


def _greeter(name: str = "Greeter", **kwargs: Any) -> Agent:
    return Agent(name=name, instructions="Greet the user.", **kwargs)


def _hand_the_process_on() -> None:
    """One Deck holds the process at a time; a handful of cases below compare two catalogs
    built back to back. They open neither deck, so there is no `aclose()` to release it."""
    Deck._release()


@pytest.fixture
def scripted():
    """Patches the model provider every real agent run in this file plays against, so a turn
    through the Runtime never reaches for a real endpoint."""
    model = ScriptedModel(deltas=["hi"])
    with patch_model(model):
        yield model


@pytest.fixture
def mcp_connect(monkeypatch):
    """Connects every MCP server the resilient transport builds without opening a socket;
    returns a `refuse.add(name)` switch to make one server's connect fail instead."""
    from agentdeck.adapters.tools.mcp import MCPServerStreamableHttpResilient

    refuse: set[str] = set()

    async def _connect(self: Any) -> None:
        if self.name in refuse:
            raise PermissionError(f"{self.name}: no")  # not transient — no retry backoff to wait out

    async def _cleanup(self: Any) -> None:
        return None

    monkeypatch.setattr(MCPServerStreamableHttpResilient, "connect", _connect)
    monkeypatch.setattr(MCPServerStreamableHttpResilient, "cleanup", _cleanup)
    return refuse


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


class _ApprovalState(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""


def _build_approval_graph() -> StateGraph:
    from langgraph.types import interrupt

    graph = StateGraph(_ApprovalState)
    graph.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    graph.add_node("approved", lambda s: {"outcome": "yes:" + s.request})
    graph.add_node("rejected", lambda s: {"outcome": "no"})
    graph.set_entry_point("ask")
    graph.add_conditional_edges(
        "ask",
        lambda s: "approved" if s.decision == "yes" else "rejected",
        {"approved": "approved", "rejected": "rejected"},
    )
    graph.add_edge("approved", END)
    graph.add_edge("rejected", END)
    return graph


def _approval_workflow(name: str = "Approval") -> Workflow:
    return Workflow(name=name, state=_ApprovalState, durable=True, graph=_build_approval_graph)


class _TimerState(BaseModel):
    woke_at: str = ""


def _timer_workflow(name: str, when: Any) -> Workflow:
    def _build_graph() -> StateGraph:
        graph = StateGraph(_TimerState)
        graph.add_node("wait", lambda s: {"woke_at": str(sleep_until(when))})
        graph.set_entry_point("wait")
        graph.add_edge("wait", END)
        return graph

    return Workflow(name=name, state=_TimerState, durable=True, graph=_build_graph)


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
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
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
    _hand_the_process_on()
    code_first = Deck(agents=[_greeter()], workflows=[_shout_workflow()], skills=Skills(root / "skills"))

    def agent_shape(agent: Agent) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
        # Same catalog, not just the same names: instructions plus every tool/handoff name,
        # so a `from_project()` that discovered the right name with the wrong body still fails.
        tools = tuple(getattr(t, "name", getattr(t, "__name__", str(t))) for t in agent.tools)
        handoffs = tuple(h if isinstance(h, str) else h.name for h in agent.handoffs)
        return agent.name, agent.instructions, tools, handoffs

    def catalog(
        deck: Deck,
    ) -> tuple[frozenset[tuple[str, str, tuple[str, ...], tuple[str, ...]]], frozenset[str], frozenset[str]]:
        return (
            frozenset(agent_shape(a) for a in deck.agents.values()),
            frozenset(deck.workflows),
            frozenset(deck.skills.build()),
        )

    assert (
        catalog(from_project)
        == catalog(code_first)
        == (
            frozenset({("Greeter", "Greet the user.", (), ())}),
            frozenset({"Shout"}),
            frozenset({"booking"}),
        )
    )


# --- one Deck per process -------------------------------------------------------------------


def _write_agent_project(root, *, bundle: str, agent: str):
    """A ``.agentdeck`` under ``root`` holding one agent named ``agent`` in ``agents/<bundle>/``."""
    project = root / ".agentdeck"
    (project / "agents" / bundle).mkdir(parents=True)
    (project / "agents" / bundle / "agent.py").write_text(
        textwrap.dedent(f"""
        from agentdeck.authoring import Agent

        it = Agent(name="{agent}", instructions="Greet the user.")
        """)
    )
    return project


@pytest.mark.asyncio
async def test_a_second_live_deck_is_refused_naming_both_projects(tmp_path):
    """Every project mounts under one module alias, so the second deck used to inherit the
    first's bundles in silence. v3 refuses it instead, and says which two projects collided."""
    alpha = _write_agent_project(tmp_path / "alpha", bundle="greeter", agent="Alpha")
    beta = _write_agent_project(tmp_path / "beta", bundle="greeter", agent="Beta")
    deck = Deck.from_project(alpha)

    with pytest.raises(ConfigError) as refused:
        Deck.from_project(beta)

    message = str(refused.value)
    assert str(alpha) in message
    assert str(beta) in message
    assert "#213" in message  # the deferred multi-deck work, so the refusal is not a dead end
    await deck.aclose()


@pytest.mark.asyncio
async def test_a_refused_second_deck_leaves_the_live_ones_namespace_alone(tmp_path):
    """The refusal must not damage the deck it protects.

    ``from_project`` mounts before it constructs, and mounting evicts the alias' cached
    submodules and repoints it at the new root — so refusing inside ``__init__`` alone would
    leave the surviving deck's namespace resolving against a project it does not own. Harmless
    while it only reads live objects, and not harmless the moment a durable workflow resumes and
    re-imports a bundle class. Hence the pre-mount refusal, pinned here.
    """
    alpha = _write_agent_project(tmp_path / "alpha", bundle="greeter", agent="Alpha")
    beta = _write_agent_project(tmp_path / "beta", bundle="greeter", agent="Beta")
    deck = Deck.from_project(alpha)
    deck.build()

    with pytest.raises(ConfigError):
        Deck.from_project(beta)

    alias = sys.modules["agentdeck_project"]
    assert alias.__path__ == [str(alpha)], "the refused project stole the alias"
    reimported = importlib.import_module("agentdeck_project.agents.greeter.agent")
    assert reimported.it.name == "Alpha", "a re-import after the refusal resolved to the wrong project"
    await deck.aclose()


@pytest.mark.parametrize("build_first", [False, True])
@pytest.mark.asyncio
async def test_a_closed_deck_hands_the_process_to_the_next_one(no_project, build_first):
    """Sequential decks are the supported shape, and `aclose()` is reachable from `NEW` and
    `BUILT` alike — a deck that was never opened still holds the process until it is closed."""
    first = Deck(agents=[_greeter()])
    if build_first:
        first.build()
    await first.aclose()

    second = Deck(agents=[_greeter(name="Second")])

    assert set(second.build().agents) == {"Second"}


def test_a_construction_that_failed_does_not_hold_the_process(no_project):
    """The claim is the last thing the constructor does: a Deck that raised on its way up never
    took the process, so one bad call cannot poison every later one."""
    with pytest.raises(ConfigError, match="Dup"):
        Deck(agents=[_greeter(name="Dup"), _greeter(name="Dup")])

    assert set(Deck(agents=[_greeter()]).agents) == {"Greeter"}


@pytest.mark.asyncio
async def test_closing_a_stale_deck_leaves_the_live_ones_claim_alone(no_project):
    """A deck only ever releases *its own* claim. The suite reaches this state through the
    internal release hook, since the constructor is what makes it unreachable otherwise."""
    stale = Deck(agents=[_greeter()])
    _hand_the_process_on()
    live = Deck(agents=[_greeter()])

    await stale.aclose()

    with pytest.raises(ConfigError, match="already live"):
        Deck(agents=[_greeter()])
    await live.aclose()


@pytest.mark.asyncio
async def test_a_sequential_deck_reads_its_own_bundles_not_the_previous_projects(tmp_path):
    """#204's own reproduction: two projects whose bundle directories share a name. Rebinding
    the alias parent leaves ``<alias>.agents.greeter.agent`` in ``sys.modules``, so without the
    eviction the second deck imports the first project's file and discovers its agent."""
    alpha = _write_agent_project(tmp_path / "alpha", bundle="greeter", agent="Alpha")
    beta = _write_agent_project(tmp_path / "beta", bundle="greeter", agent="Beta")

    first = Deck.from_project(alpha)
    assert set(first.agents) == {"Alpha"}
    await first.aclose()

    second = Deck.from_project(beta)

    assert set(second.agents) == {"Beta"}
    await second.aclose()


# --- context= is a type on the constructor and a value per run --------------------------------


def test_the_constructor_declares_a_context_type_and_the_run_supplies_the_value():
    """The parameter was deleted in #182 for being accepted and then refused at run time. It is
    back because it now does something: the type it declares is what ``build()`` checks every
    ``Context[...]`` in the catalog against (``tests/test_context_validation.py``), while the
    value still arrives per run."""
    deck = Deck(agents=[_greeter()], context=object)

    assert deck.build() is deck


@pytest.mark.asyncio
async def test_run_and_stream_accept_a_context_for_an_agent(no_project, scripted):
    """Accepted here because it arrives somewhere: a tool declaring ``Context[...]`` reads it
    (``tests/test_tool_compilation.py``). An agent with no such tool simply ignores the value."""
    deck = Deck(agents=[_greeter()])
    async with deck:
        assert (await deck.run("Greeter", "hi", context=object())).output == "hi"
        assert [event async for event in deck.stream("Greeter", "hi", context=object())]


# --- skills= and mcp= each accept a bare path and a capability object -----------------------


def test_skills_coercion_accepts_a_bare_path_and_a_capability_object(tmp_path):
    _write_skill(tmp_path, "booking")

    from_path = Deck(skills=tmp_path)
    _hand_the_process_on()
    from_object = Deck(skills=Skills(tmp_path))

    assert set(from_path.skills.build()) == set(from_object.skills.build()) == {"booking"}


def test_mcp_coercion_accepts_a_bare_path_and_a_capability_object(tmp_path):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"docs": {"url": "http://host/mcp"}}}))

    from_path = Deck(mcp=mcp_json)
    _hand_the_process_on()
    from_object = Deck(mcp=MCP(mcp_json))

    from_path.build()
    from_object.build()
    # `mcp` is deliberately not a public property (only agents/workflows/skills/settings are —
    # see the module docstring); build() succeeding without a "no mcp= configured" ConfigError
    # is itself proof the bare path coerced into a working MCP the same as the object did.


# --- root-name collisions and unknown-name references all fail build() ---------------------


def test_two_agents_sharing_a_name_raise_at_construction():
    """`{a.name: a for a in agents}` would collapse a duplicate to whichever came last with
    no error — the same silent shadow the discovery path already refuses."""
    with pytest.raises(ConfigError, match="Dup"):
        Deck(agents=[_greeter(name="Dup"), _greeter(name="Dup")])


def test_two_workflows_sharing_a_name_raise_at_construction():
    with pytest.raises(ConfigError, match="Dup"):
        Deck(workflows=[_shout_workflow(name="Dup"), _shout_workflow(name="Dup")])


def test_agent_and_workflow_sharing_a_name_fails_build_naming_both():
    deck = Deck(agents=[_greeter(name="Twin")], workflows=[_shout_workflow(name="Twin")])

    # a bare `match="Twin"` would still pass a regression to a message naming only one kind —
    # pin that both are named, not just that the shared name appears somewhere in the text.
    with pytest.raises(ConfigError, match=r"agent.*Twin.*workflow|workflow.*Twin.*agent"):
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


def test_a_durable_workflow_used_as_a_tool_fails_build_naming_both():
    """`as_tool()` calls `run(args)` with no thread_id, which a durable workflow requires, so
    the tool raised the moment a model called it — after building clean (#193)."""
    durable = Workflow(name="Durable", state=_State, graph=_build_shout_graph, durable=True)
    deck = Deck(agents=[_greeter(tools=[durable])], workflows=[durable])

    with pytest.raises(ConfigError, match="Greeter.*Durable.*durable=True"):
        deck.build()


# --- a plain callable in tools= is compiled, and one that cannot be is refused loudly ------
# (#172 rejected every bare callable; #166 makes one the canonical declaration, because a
# callable annotated Context[...] cannot be pre-decorated without leaking that parameter)


def test_agent_tool_that_is_a_bare_named_function_is_compiled():
    def lookup(q: str) -> str:
        """Look something up."""
        return q

    deck = Deck(agents=[_greeter(tools=[lookup])])
    deck.build()

    (tool,) = deck._invocables["Greeter"].native.tools
    assert tool.name == "lookup"
    assert sorted(tool.params_json_schema["properties"]) == ["q"]


def test_agent_tool_that_is_a_bare_lambda_is_compiled_under_its_own_unhelpful_name():
    """Pinned rather than policed: a lambda compiles, and the model is shown ``<lambda>``.
    Naming a tool well is the author's job, not something ``build()`` refuses over."""
    deck = Deck(agents=[_greeter(tools=[lambda q: q])])
    deck.build()

    (tool,) = deck._invocables["Greeter"].native.tools
    assert tool.name == "<lambda>"


def test_agent_tool_whose_signature_cannot_be_read_fails_build_naming_both():
    """A decorator that dropped ``functools.wraps`` leaves nothing to build a schema from — and
    nothing that could rule out a ``Context[...]`` parameter either, so compiling it anyway would
    drop that argument at the first call."""

    def destroying(fn):
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    @destroying
    def lookup(q: str) -> str:
        return q

    deck = Deck(agents=[_greeter(tools=[lookup])])

    with pytest.raises(ConfigError, match="Greeter") as exc_info:
        deck.build()
    message = str(exc_info.value)
    assert "wrapper" in message  # names the callable it was actually handed
    assert "signature could not be read" in message


def test_agent_tool_that_is_not_callable_at_all_fails_build():
    deck = Deck(agents=[_greeter(tools=["find_slots"])])

    with pytest.raises(ConfigError, match="Greeter") as exc_info:
        deck.build()
    assert "neither a callable nor an Agents SDK tool object" in str(exc_info.value)


def test_agent_tool_wrapped_with_function_tool_builds_cleanly():
    @function_tool
    def lookup(q: str) -> str:
        """Look something up."""
        return q

    deck = Deck(agents=[_greeter(tools=[lookup])])

    deck.build()  # no raise


def test_agent_tool_that_is_a_hosted_sdk_tool_builds_cleanly():
    """A ``FunctionTool``-only check would reject this — pins that the structural check
    accepts any SDK tool object, not just the one built by ``@function_tool``."""
    deck = Deck(agents=[_greeter(tools=[WebSearchTool()])])

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


# --- CLOSED is terminal: reopening a closed Deck raises rather than leaking resources -------


@pytest.mark.asyncio
async def test_reopening_a_closed_deck_raises(no_project, scripted):
    """Silently reopening would build a second runtime/store/engine set and start MCP again,
    while the eventual `aclose()` sees the stale `_closed` guard and skips draining or closing
    any of it — a leak dressed up as reuse."""
    deck = Deck(agents=[_greeter()])

    async with deck:
        await deck.run("Greeter", "hi")

    with pytest.raises(ConfigError, match="already closed"):
        async with deck:
            pass


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


@pytest.mark.asyncio
async def test_runs_namespace_ops_after_close_raise_not_open(no_project):
    """Every ``deck.runs.*`` op still forwards through ``_require_open()`` after the rename —
    a closed deck raises the same ``ConfigError`` it did when these were flat methods."""
    deck = Deck(agents=[_greeter()])

    async with deck:
        pass

    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.pause("r-1")
    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.cancel("r-1")
    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.resume("r-1")
    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.status("r-1")
    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.pending()
    with pytest.raises(ConfigError, match="not open"):
        await deck.runs.answer("r-1", "yes")


# --- opening a Deck wires the MCP servers build() only resolved as unconnected (#173) -------
#
# build() compiles every agent's mcp= against MCPLifecycle before __aenter__ has connected
# anything, so the first resolution always reads every declared server as missing. Without a
# second pass at open time, the agent that actually runs a turn is stuck with the servers it
# read as missing at build time, and its instructions falsely carry the "UNAVAILABLE" banner
# even once the server is up.


def _write_mcp_json(tmp_path) -> Any:
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"calendar": {"url": "http://host/mcp"}}}))
    return mcp_json


@pytest.mark.asyncio
@pytest.mark.parametrize("build_first", [False, True], ids=["direct-open", "build-then-open"])
async def test_opening_a_deck_wires_its_connected_mcp_server_into_the_compiled_agent(
    no_project, tmp_path, mcp_connect, build_first
):
    deck = Deck(agents=[_greeter(mcp=["calendar"])], mcp=_write_mcp_json(tmp_path))
    if build_first:
        deck.build()  # the documented pattern: validate early, open later — must not stick

    async with deck:
        compiled = deck._invocables["Greeter"].native
        assert [server.name for server in compiled.mcp_servers] == ["calendar"]
        assert compiled.instructions == "Greet the user."  # no banner: the server is up


@pytest.mark.asyncio
async def test_a_refused_mcp_connect_still_leaves_the_degraded_banner_after_open(no_project, tmp_path, mcp_connect):
    mcp_connect.add("calendar")
    deck = Deck(agents=[_greeter(mcp=["calendar"])], mcp=_write_mcp_json(tmp_path))

    async with deck:
        compiled = deck._invocables["Greeter"].native
        assert compiled.mcp_servers == []
        assert "UNAVAILABLE: `calendar`" in compiled.instructions


@pytest.mark.asyncio
async def test_mcp_refresh_keeps_a_skills_disclosure_intact(no_project, tmp_path, mcp_connect):
    _write_skill(tmp_path, "booking")
    reference = Deck(agents=[_greeter(skills=["booking"])], skills=tmp_path)
    reference.build()
    expected_instructions = reference._invocables["Greeter"].native.instructions

    _hand_the_process_on()
    deck = Deck(agents=[_greeter(skills=["booking"], mcp=["calendar"])], skills=tmp_path, mcp=_write_mcp_json(tmp_path))
    deck.build()
    stale = deck._invocables["Greeter"].native.instructions
    assert stale != expected_instructions  # the banner is baked in before anything connects
    assert "UNAVAILABLE" in stale

    async with deck:
        refreshed = deck._invocables["Greeter"].native.instructions

    assert refreshed == expected_instructions  # banner gone, disclosure intact either way


def test_build_logs_no_false_not_found_warning_for_a_configured_mcp_server(tmp_path, caplog):
    """Before the fix, resolving a genuinely-configured server before any server connects
    fell back to a bare, un-configured ``MCPLifecycle`` and logged this exact line — the
    silent-drop wording #173 flags, even though the server is not, in fact, missing."""
    deck = Deck(agents=[_greeter(mcp=["calendar"])], mcp=_write_mcp_json(tmp_path))

    with caplog.at_level("WARNING"):
        deck.build()

    assert not any("not found in config" in record.message for record in caplog.records)


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


@pytest.mark.asyncio
async def test_deck_closes_a_store_it_built_itself(no_project, monkeypatch, scripted):
    """The other half of the ownership rule: dropping `_owns_store`'s branch entirely would
    still pass every other test here, since none of them build a store worth spying on."""
    from agentdeck.adapters.stores.memory import MemoryEventStore

    class _SpyStore(MemoryEventStore):
        def __init__(self) -> None:
            super().__init__()
            self.aclose_calls = 0

        async def aclose(self) -> None:
            self.aclose_calls += 1

    store = _SpyStore()
    monkeypatch.setattr("agentdeck.deck.resolve_event_store", lambda: store)
    deck = Deck(agents=[_greeter()])

    async with deck:
        await deck.run("Greeter", "hi")

    assert store.aclose_calls == 1


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


# --- v1's convenience carried across as `run`/`stream`, behave the same on Deck ------------


def _reader_ctx(session_id: str | None) -> RunContext:
    """A throwaway context of the Deck's own namespace, for reading its log back in a test —
    exactly what ``serve.py``'s compat routes build for an HTTP request."""
    return RunContext(run_id="reader", session_id=session_id)


@pytest.mark.asyncio
async def test_run_with_no_input_defaults_a_workflow_to_an_empty_object(no_project, monkeypatch):
    """``state=None``'s old meaning ("no updates") has to survive wrapping it in a
    ``DataBlock``, which cannot carry ``None`` as a graph's state."""
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    deck = Deck(workflows=[_shout_workflow()])

    async with deck:
        out = await deck.run("Shout", None)

    assert out == {"shouted": ""}


@pytest.mark.asyncio
async def test_run_is_recorded_and_returns_a_turn_result(no_project, scripted):
    deck = Deck(agents=[_greeter()])

    async with deck:
        result = await deck.run("Greeter", "hello")
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
async def test_run_with_a_session_id_is_recorded_and_returns_a_turn_result(no_project, scripted):
    deck = Deck(agents=[_greeter()])

    async with deck:
        result = await deck.run("Greeter", "hello", session_id="s1")
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
async def test_a_failed_run_still_leaves_run_failed_in_the_log(no_project):
    """A run that raises is still written down, even though nobody read the stream to the end
    by hand."""
    deck = Deck(agents=[_greeter()])

    with patch_model(ScriptedModel(deltas=("par",), raises=RuntimeError("boom"))):
        async with deck:
            with pytest.raises(RuntimeError, match="boom"):
                await deck.run("Greeter", "hello", session_id="s1")
            events = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert [event.kind for event in events] == ["run.started", "text.delta", "run.failed"]


class _Greeting(BaseModel):
    greeting: str


@pytest.mark.asyncio
async def test_a_structured_run_output_survives_as_validated_data(no_project):
    """An ``output_type`` result rides ``run.completed`` as a ``DataBlock``, and ``run``'s
    ``TurnResult`` must surface it as data rather than joining it as text."""
    deck = Deck(agents=[Agent(name="Structured", instructions="Answer as JSON.", output_type=_Greeting)])

    with patch_model(ScriptedModel(deltas=('{"greeting": "hi"}',))):
        async with deck:
            result = await deck.run("Structured", "hello", session_id="s1")

    assert result.output == {"greeting": "hi"}


@pytest.mark.asyncio
async def test_stream_yields_canonical_events_and_is_recorded(no_project):
    from agentdeck.core.events import RunCompleted, TextDelta

    deck = Deck(agents=[_greeter()])

    with patch_model(ScriptedModel(deltas=("Hel", "lo"))):
        async with deck:
            events = [event async for event in deck.stream("Greeter", "hello", session_id="s1")]

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
            # rather than the caller having to trust stream's own bookkeeping
            stored = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert [event.kind for event in stored] == [event.kind for event in events]


@pytest.mark.asyncio
async def test_stream_closes_the_runtime_generator_on_abandonment(no_project):
    """A caller that stops mid-stream must not leave the run open in the log holding its
    session forever: closing only ``stream``'s own frame would abandon the Runtime's
    generator to the GC instead of closing it."""
    deck = Deck(agents=[_greeter()])

    with patch_model(ScriptedModel(deltas=("Hel", "lo"))):
        async with deck:
            stream = deck.stream("Greeter", "hello", session_id="s1")
            first = await anext(stream)
            second = await anext(stream)
            await stream.aclose()
            events = await deck._runtime.store.read("s1", _reader_ctx("s1"))

    assert (first.kind, second.kind) == ("run.started", "text.delta")
    assert [event.kind for event in events] == ["run.started", "text.delta", "run.cancelled"]


@pytest.mark.asyncio
async def test_run_and_stream_share_one_session(no_project):
    """Same guarantee v1 gave: one ``session_id`` is one conversation whichever Deck method ran
    the turn."""
    model = ScriptedModel(deltas=("hi",))
    deck = Deck(agents=[_greeter()])

    with patch_model(model):
        async with deck:
            async for _ in deck.stream("Greeter", "first", session_id="s1"):
                pass
            await deck.run("Greeter", "second", session_id="s1")

    # two model calls, and the second turn's input carries the first turn's history
    assert model.calls == 2
    assert "first" in str(model.inputs[-1])


def test_sessions_keyed_by_id(no_project):
    deck = Deck(agents=[_greeter()])

    assert deck.session_for("a") is deck.session_for("a")
    assert deck.session_for("a") is not deck.session_for("b")


# --- runs.pending() lists a paused run, runs.answer() answers it by run_id ----------------


@pytest.mark.asyncio
async def test_pending_lists_a_paused_run_and_answer_completes_it(no_project, monkeypatch):
    """``runs.answer`` pairs with ``runs.pending``: a caller lists the inbox, then answers one
    run by the ``run_id`` it named there — no name, thread id, or session id supplied by hand."""
    from agentdeck.runtime.settings import reset_settings_cache

    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    reset_settings_cache()
    try:
        deck = Deck(workflows=[_approval_workflow()])

        async with deck:
            paused = await deck.run("Approval", {"request": "tue 9am"}, session_id="t-1")
            assert paused["type"] == "interrupt"

            [pending] = await deck.runs.pending()
            assert pending.run_id  # minted by the Runtime, not supplied by the caller
            assert pending.thread_id == "t-1"

            result = await deck.runs.answer(pending.run_id, "yes")
    finally:
        reset_settings_cache()

    assert result == {"request": "tue 9am", "decision": "yes", "outcome": "yes:tue 9am"}


@pytest.mark.asyncio
async def test_answer_of_an_unknown_run_id_raises_not_found(no_project):
    deck = Deck(workflows=[_approval_workflow()])

    async with deck:
        with pytest.raises(NotFoundError, match="nonexistent"):
            await deck.runs.answer("nonexistent", "yes")


# --- _tick() resumes a Deck.run() interrupt through the Runtime, not a checkpointer ghost ----


@pytest.mark.asyncio
async def test_tick_resumes_a_deck_run_interrupt_through_the_runtime_not_a_ghost(no_project, monkeypatch, caplog):
    """A timer interrupt ``Deck.run()`` parked is the same thread ``Deck._tick()`` finds on the
    checkpointer — the log and the checkpointer already agree on the *listing* (direction one,
    already true post-#164). Resuming it by calling the workflow directly, as ``_tick()`` used
    to, never told the event log: the run stayed ``WAITING_ANSWER`` forever and kept holding its
    session claim (direction two — the still-live half of #120). ``_tick()`` must resume through
    the Runtime instead, so the log's run closes and a fresh run on the same thread does not
    hit a stale-lock ``SessionBusyError``.
    """
    from agentdeck.runtime.settings import reset_settings_cache

    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    reset_settings_cache()
    past = datetime.now(UTC) - timedelta(days=1)
    try:
        deck = Deck(workflows=[_timer_workflow("Timer", past)])

        async with deck:
            paused = await deck.run("Timer", {}, session_id="t-tick")
            assert paused["type"] == "interrupt"

            # direction 1: the checkpointer-sourced listing already sees a Deck.run() interrupt.
            due = await deck._due_resumes()
            assert [d["thread_id"] for d in due] == ["t-tick"]

            with caplog.at_level("WARNING"):
                finished = await deck._tick()
            # the reconciled resume passes the interrupt payload's own ISO string, not the
            # parsed datetime, so the Runtime logs the answer cleanly instead of warning that
            # a datetime "cannot be held" and dropping it. `agentdeck.composition`'s own
            # memory-backend warning (issue #155), logged once when `Deck` opened above, is
            # expected and unrelated to what this assertion is checking.
            assert finished == [{"woke_at": past.isoformat()}]
            other_warnings = [r for r in caplog.records if r.name != "agentdeck.composition"]
            assert not other_warnings, [r.message for r in other_warnings]

            # direction 2: resuming through _tick() must close the *logged* run too, not just
            # the checkpoint — a ghost WAITING_ANSWER entry is exactly what this pins against.
            assert await deck.runs.pending() == []

            # ...and release the session claim it was holding, not leave it stale-locked.
            again = await deck.run("Timer", {}, session_id="t-tick")
            assert again["type"] == "interrupt"
    finally:
        reset_settings_cache()


@pytest.mark.asyncio
async def test_pause_and_resume_reach_the_runtime_this_deck_composed(no_project, scripted):
    """The wiring, end to end and with nothing hand-built: ``Deck.runs.pause`` writes to the
    very control port this Deck's own Runtime got, the run stops at its own safe point, and
    ``Deck.runs.resume`` plays it on to completion."""
    deck = Deck(agents=[_greeter()])

    async with deck:
        run_id = "r-control"
        assert await deck.runs.pause(run_id, "operator stepped away") is True
        paused = [event async for event in deck.stream("Greeter", "hi there", run_id=run_id)]
        resumed = await deck.runs.resume(run_id)

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
