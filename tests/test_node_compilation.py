"""Compiling a workflow node into one langgraph can call, and the ``context=`` that reaches it.

The langgraph counterpart of ``tests/test_tool_compilation.py``. Two things it exists to pin
that the shared contract suite cannot:

* **State and context are separate, and neither absorbs the other.** A node holds both, and the
  only way to tell a real separation from an accident is to mutate each and look at the other.
* **A resume resupplies the context.** The value is never serialized, so nothing in the log can
  be compared against what should have been there — a node reading ``ctx.data`` after a resume
  that lost it degrades in silence. These tests fail against a ``resume`` that mints its
  ``RunContext`` without ``data=``, which is what it did before this slice.

No live model anywhere here: a workflow node calls none.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from agentdeck.adapters.engines.langgraph.engine import REPORTER_KEY
from agentdeck.authoring import Workflow
from agentdeck.authoring.graphs import bridge_context_nodes
from agentdeck.core.context import Context  # noqa: TC001 — the nodes below must resolve it at runtime
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError
from agentdeck.runtime.settings import reset_settings_cache


class Calendar:
    """The application object a run is handed."""

    def __init__(self, slot: str = "09:00") -> None:
        self.slot = slot
        self.booked: list[str] = []


class _State(BaseModel):
    request: str = ""
    out: str = ""
    decision: str = ""


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def memory_checkpointer(monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _workflow(node: Any, *, name: str = "Book", durable: bool = False) -> Workflow:
    def build() -> StateGraph:
        graph = StateGraph(_State)
        graph.add_node("book", node)
        graph.set_entry_point("book")
        graph.add_edge("book", END)
        return graph

    return Workflow(name=name, state=_State, durable=durable, graph=build)


# --- the context reaches a node, alongside its state ---------------------------------------------


@pytest.mark.asyncio
async def test_a_node_declaring_a_context_receives_the_object_the_run_was_given(no_project) -> None:
    seen: list[Any] = []

    async def book(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        seen.append(environment.data)
        return {"out": f"{state.request}@{environment.data.slot}"}

    calendar = Calendar(slot="14:00")
    deck = Deck(workflows=[_workflow(book)])
    deck.build()

    async with deck:
        result = await deck.run("Book", {"request": "tue"}, context=calendar)

    assert result["out"] == "tue@14:00"
    assert seen[0] is calendar


@pytest.mark.asyncio
async def test_state_and_context_are_separate_and_neither_absorbs_the_other(no_project) -> None:
    """The design's rule stated as an experiment: the node mutates the environment and returns a
    state patch, and afterwards each holds only its own change. A bridge that folded the context
    into state (or read state back out of it) fails one half or the other."""

    async def book(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        environment.data.booked.append(state.request)
        return {"out": "written to state only"}

    calendar = Calendar()
    deck = Deck(workflows=[_workflow(book)])
    deck.build()

    async with deck:
        result = await deck.run("Book", {"request": "tue"}, context=calendar)

    # The state patch went nowhere near the environment...
    assert calendar.booked == ["tue"]
    assert not hasattr(calendar, "out")
    # ...and the environment's mutation went nowhere near the state.
    assert result["out"] == "written to state only"
    assert "booked" not in result


@pytest.mark.asyncio
async def test_a_sync_node_declaring_a_context_runs_off_the_event_loop(no_project) -> None:
    """Parity with what langgraph does for a sync node: a blocking body must not run on the
    loop, where it would stall the stream and every safe point with it."""
    import threading

    ran_on: list[threading.Thread] = []

    def book(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        ran_on.append(threading.current_thread())
        return {"out": environment.data.slot}

    deck = Deck(workflows=[_workflow(book)])
    deck.build()

    async with deck:
        result = await deck.run("Book", {"request": "tue"}, context=Calendar(slot="16:00"))

    assert result["out"] == "16:00"
    assert ran_on[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_a_node_keeps_the_langgraph_parameters_it_also_declared(no_project) -> None:
    """``reporter`` stays on ``configurable`` (the wider question is #211), so a node reaching it
    through ``config`` must keep working next to an injected context — the bridge forwards every
    parameter langgraph fills rather than swallowing them."""
    reached: list[Any] = []

    async def book(state: _State, config: RunnableConfig, environment: Context[Calendar]) -> dict[str, Any]:
        reached.append(config["configurable"][REPORTER_KEY])
        return {"out": environment.data.slot}

    deck = Deck(workflows=[_workflow(book)])
    deck.build()

    async with deck:
        result = await deck.run("Book", {"request": "tue"}, context=Calendar(slot="10:00"))

    assert result["out"] == "10:00"
    assert reached and reached[0] is not None


# --- what is left alone --------------------------------------------------------------------------


def test_a_node_declaring_no_context_is_not_rewritten() -> None:
    """The bridge is a no-op for every workflow written before this existed."""

    def plain(state: _State) -> dict[str, Any]:
        return {"out": "plain"}

    graph = StateGraph(_State)
    graph.add_node("book", plain)
    before = graph.nodes["book"]

    assert bridge_context_nodes(graph).nodes["book"] is before


def test_a_node_that_is_an_engine_native_runnable_is_left_alone() -> None:
    """Two validation levels: an opaque node is engine-native, and nothing here pretends it can
    introspect one."""
    graph = StateGraph(_State)
    graph.add_node("book", RunnableLambda(lambda state: {"out": "native"}))
    before = graph.nodes["book"]

    assert bridge_context_nodes(graph).nodes["book"] is before


def test_a_node_whose_signature_cannot_be_read_is_left_alone_rather_than_refused() -> None:
    """Divergence from a *tool*, on purpose: a tool must publish a model-visible schema at build
    time, so an unreadable one has nothing honest to offer and is refused. A node publishes no
    schema, so leaving it exactly as langgraph would have run it changes nothing that works
    today — and refusing would break graphs that never wanted a context at all."""

    def destroying(fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    @destroying
    def book(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        return {"out": "never"}

    graph = StateGraph(_State)
    graph.add_node("book", book)
    before = graph.nodes["book"]

    assert bridge_context_nodes(graph).nodes["book"] is before


def test_two_context_parameters_on_a_node_fail_at_build_naming_the_node() -> None:
    """The same failure a tool's gets, at the same moment — ``build()``, not the first run."""

    def book(state: _State, here: Context[Calendar], also: Context[Calendar]) -> dict[str, Any]:
        return {}

    deck = Deck(workflows=[_workflow(book)])

    with pytest.raises(ConfigError) as raised:
        deck.build()

    message = str(raised.value)
    assert "node 'book'" in message
    assert "at most one" in message


@pytest.mark.asyncio
async def test_a_bridged_node_played_by_a_foreign_run_says_so(no_project) -> None:
    """The invocation-time safety net, the node's version: a graph compiled by AgentDeck and
    then invoked by langgraph directly has no run context to unwrap."""

    async def book(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        return {"out": "ran"}

    graph = bridge_context_nodes(_workflow(book).build_graph())

    with pytest.raises(ConfigError, match="AgentDeck run context"):
        await graph.compile().ainvoke({"request": "tue"})


# --- resume resupplies the context ------------------------------------------------------------------


def _approval_workflow(seen: list[Any]) -> Workflow:
    """Pauses on ``interrupt()``, then reads its environment. The node re-runs from its start on
    resume, so ``seen`` records what the context was on each pass."""

    async def ask(state: _State, environment: Context[Calendar]) -> dict[str, Any]:
        seen.append(environment.data)
        decision = interrupt({"question": state.request})
        return {"decision": decision, "out": f"{decision}@{environment.data.slot}"}

    return _workflow(ask, name="Approval", durable=True)


@pytest.mark.asyncio
async def test_answer_resupplies_the_context_to_the_node_that_re_runs(no_project, memory_checkpointer) -> None:
    """The bug this slice closes. ``answer`` mints a fresh ``RunContext``; before this, it minted
    one with no ``data=`` at all, so the re-running node read ``None`` and a defensive node would
    have returned a plausible wrong answer with nothing in the log to contradict it."""
    seen: list[Any] = []
    calendar = Calendar(slot="15:00")
    deck = Deck(workflows=[_approval_workflow(seen)])
    deck.build()

    async with deck:
        paused = await deck.run("Approval", {"request": "tue 9am"}, session_id="t-1", context=calendar)
        assert paused["type"] == "interrupt"
        [pending] = await deck.pending()
        result = await deck.answer(pending.run_id, "yes", context=calendar)

    assert result["out"] == "yes@15:00"
    # Once before the interrupt and once after: both passes saw the very same object.
    assert seen == [calendar, calendar]
    assert seen[1] is calendar


@pytest.mark.asyncio
async def test_answering_without_a_context_resumes_with_none_rather_than_the_old_value(
    no_project, memory_checkpointer
) -> None:
    """Resupplied, never recovered. Omitting it is not "keep what the run had" — the value was
    never written down, so there is nothing to keep, and this states which of the two it is."""
    seen: list[Any] = []
    deck = Deck(workflows=[_approval_workflow(seen)])
    deck.build()

    async with deck:
        await deck.run("Approval", {"request": "tue 9am"}, session_id="t-1", context=Calendar())
        [pending] = await deck.pending()
        with pytest.raises(AttributeError):  # the node reads ``.slot`` off ``None``
            await deck.answer(pending.run_id, "yes")

    assert seen[-1] is None


# ``Runtime.resume_run`` — the *pause* path rather than the interrupt path — mints its context
# separately and lost it the same way. It is covered where the pause machinery lives, in
# ``tests/test_run_control.py``, because only an agent run reaches a safe point today.
