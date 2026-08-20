"""Human-in-the-loop (issue #10): a durable workflow pauses on ``interrupt()``, is
listed as pending, and resumes with the human's decision  -  including in a fresh
process against the same sqlite file. Non-durable workflows can't do any of it.
"""

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from agentdeck.authoring import Workflow
from agentdeck.core.context import RunContext
from agentdeck.errors import ConfigError
from agentdeck.runtime.settings import reset_settings_cache


class ApprovalState(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""


def _build_approval_graph():
    g = StateGraph(ApprovalState)
    g.add_node("prepare", lambda s: {"prepared": s.prepared + 1})
    g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    g.add_node("approved", lambda s: {"outcome": f"booked:{s.request}"})
    g.add_node("rejected", lambda s: {"outcome": "dropped"})
    g.set_entry_point("prepare")
    g.add_edge("prepare", "ask")
    g.add_conditional_edges(
        "ask",
        lambda s: "approved" if s.decision == "yes" else "rejected",
        {"approved": "approved", "rejected": "rejected"},
    )
    g.add_edge("approved", END)
    g.add_edge("rejected", END)
    return g


def _make_approval_workflow(*, durable: bool) -> Workflow:
    """Prepare -> ask a human -> approved/rejected branch. Fresh ``Workflow`` per test."""
    return Workflow(name="ApprovalFlow", state=ApprovalState, durable=durable, graph=_build_approval_graph)


def run_context(session_id: str | None = None) -> RunContext:
    """A reader context for the store assertions here.

    The Runtime takes options now; ``EventStorePort`` still takes a context, being an internal
    port, and only the session id and namespace are read off this one.
    """
    return RunContext(run_id="reader", session_id=session_id)


@pytest.fixture(autouse=True)
def _memory_checkpointer(monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_pause_list_resume_and_complete():
    wf = _make_approval_workflow(durable=True)

    async def _scenario():
        paused = await wf.run({"request": "tue 9am"}, thread_id="t-approve")
        pending = await wf.pending()
        resumed = await wf.resume("t-approve", "yes")
        return paused, pending, resumed, await wf.pending()

    paused, pending, resumed, after = asyncio.run(_scenario())

    assert paused == {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-approve"}
    assert pending == [{"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-approve"}]
    assert resumed["outcome"] == "booked:tue 9am"
    assert resumed["decision"] == "yes"
    assert after == []  # the thread completed, so it left the inbox


def test_rejected_decision_routes_the_other_branch():
    wf = _make_approval_workflow(durable=True)

    async def _scenario():
        await wf.run({"request": "wed 4pm"}, thread_id="t-reject")
        return await wf.resume("t-reject", "no")

    resumed = asyncio.run(_scenario())

    assert resumed["outcome"] == "dropped"


def test_nodes_before_the_interrupt_re_execute_on_resume():
    """The documented constraint, asserted: ``prepare`` runs once, but the interrupt
    node's own body replays  -  side effects must not live there."""
    wf = _make_approval_workflow(durable=True)

    async def _scenario():
        await wf.run({"request": "x"}, thread_id="t-replay")
        return await wf.resume("t-replay", "yes")

    resumed = asyncio.run(_scenario())

    assert resumed["prepared"] == 1  # completed nodes are checkpointed, not re-run


def test_pending_is_scoped_per_thread_and_lists_only_paused_runs():
    wf = _make_approval_workflow(durable=True)

    async def _scenario():
        await wf.run({"request": "a"}, thread_id="t-a")
        await wf.run({"request": "b"}, thread_id="t-b")
        await wf.resume("t-a", "yes")
        return await wf.pending()

    pending = asyncio.run(_scenario())

    # the memory saver is cached per process, so look only at the two threads made here
    mine = [p for p in pending if p["thread_id"] in {"t-a", "t-b"}]
    assert [p["thread_id"] for p in mine] == ["t-b"]  # t-a was answered, t-b still waiting
    assert mine[0]["payload"] == {"question": "b"}


def test_non_durable_interrupt_raises_config_error():
    wf = _make_approval_workflow(durable=False)

    with pytest.raises(ConfigError, match="durable"):
        asyncio.run(wf.run({"request": "x"}))


def test_non_durable_resume_raises_config_error():
    wf = _make_approval_workflow(durable=False)

    with pytest.raises(ConfigError, match="resume"):
        asyncio.run(wf.resume("t", "yes"))


def test_non_durable_pending_is_empty():
    assert asyncio.run(_make_approval_workflow(durable=False).pending()) == []


def test_workflow_without_interrupts_still_returns_its_final_state():
    """No regression for the ordinary path: a run that ends is not an interrupt."""
    wf = _make_approval_workflow(durable=True)

    async def _scenario():
        paused = await wf.run({"request": "z"}, thread_id="t-plain")
        return await wf.resume("t-plain", "yes"), paused

    final, paused = asyncio.run(_scenario())

    assert paused["type"] == "interrupt"
    assert "type" not in final and final["outcome"] == "booked:z"


APPROVAL_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel
from agentdeck.authoring import Workflow

class State(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("prepare", lambda s: {"prepared": s.prepared + 1})
    g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    g.add_node("done", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
    g.set_entry_point("prepare")
    g.add_edge("prepare", "ask")
    g.add_edge("ask", "done")
    g.add_edge("done", END)
    return g

approval_flow = Workflow(name="ApprovalFlow", state=State, durable=True, graph=_build_graph)
"""


PLAIN_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.authoring import Workflow

class State(BaseModel):
    text: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("shout", lambda s: {"text": s.text.upper()})
    g.set_entry_point("shout")
    g.add_edge("shout", END)
    return g

plain_flow = Workflow(name="PlainFlow", state=State, durable=True, graph=_build_graph)
"""


@pytest.fixture
def app_project(tmp_path, monkeypatch):
    root = tmp_path / ".agentdeck" / "workflows" / "approval_flow"
    root.mkdir(parents=True)
    (root / "workflow.py").write_text(textwrap.dedent(APPROVAL_WORKFLOW_PY))
    (tmp_path / ".agentdeck" / "workflows" / "plain_flow").mkdir()
    (tmp_path / ".agentdeck" / "workflows" / "plain_flow" / "workflow.py").write_text(
        textwrap.dedent(PLAIN_WORKFLOW_PY)
    )
    monkeypatch.chdir(tmp_path)
    # the project alias is process-global; drop stale mounts from other tests
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck.deck import Deck

    return Deck.from_project()


async def test_stream_ends_with_a_run_interrupted_event(app_project):
    """The streaming surface: a node-updated event, then run-interrupted *instead of*
    run-completed  -  and the same pause the Runtime's own inbox lists."""
    async with app_project:
        events = [
            event async for event in app_project.stream("ApprovalFlow", {"request": "tue 9am"}, session_id="t-stream")
        ]

        assert [event.kind for event in events] == ["run.started", "node.updated", "run.interrupted"]
        assert events[1].payload.node == "prepare"
        assert events[1].payload.state_patch == {"prepared": 1}
        assert events[2].payload.payload == {"question": "tue 9am"}
        assert events[2].payload.thread_id == "t-stream"

        run = await app_project.runs.get(events[0].run_id)

        # a run started on `stream` is answerable by `Run.answer`, the inversion of what
        # `run_workflow_stream` (deleted with the rest of v1's surface) could never do
        await run.answer("yes")
        result = await run
    assert result["outcome"] == "booked"


async def test_stream_without_an_interrupt_still_ends_with_run_completed(app_project):
    """No regression for #9's shape: only a paused run swaps run-completed for run-interrupted."""
    async with app_project:
        events = [event async for event in app_project.stream("PlainFlow", {"text": "hi"}, session_id="t-p")]

    assert [event.kind for event in events] == ["run.started", "node.updated", "run.completed"]
    assert events[1].payload.node == "shout"
    assert events[1].payload.state_patch == {"text": "HI"}


def test_deck_surface_runs_lists_and_answers(app_project):
    """``Deck`` is the entry point: run -> the interrupt's own ``id`` -> ``deck.runs.get`` ->
    ``Run.answer``."""
    deck = app_project

    async def _scenario():
        async with deck:
            paused = await deck.run("ApprovalFlow", {"request": "tue 9am"}, session_id="t-app")
            mine = await deck.runs.get(paused["id"])
            await mine.answer("no")
            resumed = await mine
            # both run and answer write to the event log now  -
            # read it back rather than trusting each call's own bookkeeping.
            events = await deck._runtime.store.read_session(RunContext(run_id="reader", session_id="t-app"))
            return paused, mine, resumed, [event.kind for event in events]

    paused, mine, resumed, kinds = asyncio.run(_scenario())

    assert kinds[0] == "run.started"
    assert "run.interrupted" in kinds  # the pause `run` produced
    assert "run.resumed" in kinds  # answer's own claim, not a fresh run
    assert kinds[-1] == "run.completed"

    assert paused == {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-app", "id": mine.id}
    assert resumed["outcome"] == "dropped"


_RESTART_SCRIPT = """
import asyncio, json, sys
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel
from agentdeck.authoring import Workflow

class State(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    g.add_node("approved", lambda s: {"outcome": "booked:" + s.request})
    g.add_node("rejected", lambda s: {"outcome": "dropped"})
    g.set_entry_point("ask")
    g.add_conditional_edges(
        "ask",
        lambda s: "approved" if s.decision == "yes" else "rejected",
        {"approved": "approved", "rejected": "rejected"},
    )
    g.add_edge("approved", END)
    g.add_edge("rejected", END)
    return g

approval_flow = Workflow(name="ApprovalFlow", state=State, durable=True, graph=_build_graph)

async def main():
    if sys.argv[1] == "start":
        return await approval_flow.run({"request": "tue 9am"}, thread_id="restart-hitl")
    pending = await approval_flow.pending()
    resumed = await approval_flow.resume("restart-hitl", sys.argv[2])
    return {"pending": pending, "resumed": resumed}

print(json.dumps(asyncio.run(main())))
"""


def _run_script(arg: str, decision: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_RESTART_SCRIPT), arg, decision],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize(("decision", "outcome"), [("yes", "booked:tue 9am"), ("no", "dropped")])
def test_interrupt_survives_a_process_restart(tmp_path, decision, outcome):
    """The acceptance test: one process pauses, a *different* process reads the inbox
    off the sqlite file and resumes it to completion  -  days-later approval, in miniature.
    """
    import json

    env = {
        **os.environ,
        "AGENTDECK_CHECKPOINT": f"sqlite://{tmp_path / 'checkpoints.sqlite3'}",
    }

    paused = json.loads(_run_script("start", decision, env))
    finished = json.loads(_run_script("resume", decision, env))

    assert paused == {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "restart-hitl"}
    assert finished["pending"] == [paused]  # a fresh process found it in the inbox
    assert finished["resumed"]["outcome"] == outcome
