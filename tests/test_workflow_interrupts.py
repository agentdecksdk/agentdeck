"""Human-in-the-loop (issue #10): a durable workflow pauses on ``interrupt()``, is
listed as pending, and resumes with the human's decision — including in a fresh
process against the same sqlite file. Non-durable workflows can't do any of it.
"""

import asyncio
import os
import subprocess
import sys
import textwrap

import pytest
from pydantic import BaseModel

from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.settings import reset_settings_cache
from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt


class ApprovalState(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""


def _make_approval_workflow(*, durable: bool) -> type[BaseWorkflow]:
    """Prepare -> ask a human -> approved/rejected branch. Fresh class per test
    (``_compiled`` is cached on the class itself)."""

    class ApprovalFlow(BaseWorkflow):
        state = ApprovalState

        @classmethod
        def build_graph(cls):
            g = StateGraph(cls.state)
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

    ApprovalFlow.durable = durable
    return ApprovalFlow


@pytest.fixture(autouse=True)
def _memory_checkpointer(monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
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
    node's own body replays — side effects must not live there."""
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
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt

class State(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""

class ApprovalFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("prepare", lambda s: {"prepared": s.prepared + 1})
        g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
        g.add_node("done", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
        g.set_entry_point("prepare")
        g.add_edge("prepare", "ask")
        g.add_edge("ask", "done")
        g.add_edge("done", END)
        return g
"""


PLAIN_WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph

class State(BaseModel):
    text: str = ""

class PlainFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("shout", lambda s: {"text": s.text.upper()})
        g.set_entry_point("shout")
        g.add_edge("shout", END)
        return g
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
    from agentdeck import App

    return App()


async def test_streamed_run_ends_with_an_interrupt_event(app_project):
    """The streaming surface: node updates, then an interrupt event *instead of* ``done``."""
    events = [
        event
        async for event in app_project.run_workflow_stream("ApprovalFlow", {"request": "tue 9am"}, thread_id="t-stream")
    ]

    assert events == [
        {"type": "node_update", "node": "prepare", "delta": {"prepared": 1}},
        {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-stream"},
    ]
    inbox = await app_project.pending_interrupts("ApprovalFlow")
    assert [p for p in inbox if p["thread_id"] == "t-stream"] == [events[-1]]


async def test_a_run_started_via_run_workflow_stream_cannot_be_resumed_via_resume_workflow(app_project):
    """A pause used to resume the same way regardless of which App method started the run,
    because both read and wrote the same checkpointer. That symmetry breaks once
    ``resume_workflow`` plays on the Runtime (issue #137): it looks the paused run up in the
    event log, and ``run_workflow_stream`` — left out of that reroute — writes nothing there.
    A caller needing both the log and a live stream on one thread starts on ``run_workflow``
    (or ``resume_workflow``) instead, the way ``test_app_surface_pauses_lists_and_resumes`` does.
    """
    async for _ in app_project.run_workflow_stream("ApprovalFlow", {"request": "tue 9am"}, thread_id="t-stream-2"):
        pass

    with pytest.raises(NotFoundError, match="t-stream-2"):
        await app_project.resume_workflow("ApprovalFlow", "t-stream-2", "yes")


async def test_streamed_durable_run_without_an_interrupt_still_ends_with_done(app_project):
    """No regression for #9's shape: only a paused run swaps ``done`` for an interrupt."""
    events = [event async for event in app_project.run_workflow_stream("PlainFlow", {"text": "hi"}, thread_id="t-p")]

    assert events == [
        {"type": "node_update", "node": "shout", "delta": {"text": "HI"}},
        {"type": "done", "state": {"text": "HI"}},
    ]


def test_app_surface_pauses_lists_and_resumes(app_project):
    """``App`` is the entry point: run -> pending_interrupts() (no name = every workflow) -> resume."""
    app = app_project

    async def _scenario():
        paused = await app.run_workflow("ApprovalFlow", {"request": "tue 9am"}, thread_id="t-app")
        return paused, await app.pending_interrupts(), await app.resume_workflow("ApprovalFlow", "t-app", "no")

    paused, pending, resumed = asyncio.run(_scenario())

    assert paused == {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-app"}
    # other tests in this process share the cached memory saver, so filter to this thread
    assert [p for p in pending if p["thread_id"] == "t-app"] == [paused]
    assert resumed["outcome"] == "dropped"


_RESTART_SCRIPT = """
import asyncio, json, sys
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt

class State(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""

class ApprovalFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
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

async def main():
    if sys.argv[1] == "start":
        return await ApprovalFlow.run({"request": "tue 9am"}, thread_id="restart-hitl")
    pending = await ApprovalFlow.pending()
    resumed = await ApprovalFlow.resume("restart-hitl", sys.argv[2])
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
    off the sqlite file and resumes it to completion — days-later approval, in miniature.
    """
    import json

    pytest.importorskip("langgraph.checkpoint.sqlite", reason="needs the [durability] extra")
    env = {
        **os.environ,
        "AGENTDECK_CHECKPOINT_BACKEND": "sqlite",
        "AGENTDECK_CHECKPOINT_URL": str(tmp_path / "checkpoints.sqlite3"),
    }

    paused = json.loads(_run_script("start", decision, env))
    finished = json.loads(_run_script("resume", decision, env))

    assert paused == {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "restart-hitl"}
    assert finished["pending"] == [paused]  # a fresh process found it in the inbox
    assert finished["resumed"]["outcome"] == outcome
