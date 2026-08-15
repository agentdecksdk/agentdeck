"""Durable timer waits (issue #22): sleep_until pauses a durable workflow until a wall-clock
moment; Deck._tick() resumes threads whose wake time has passed. Reuses the #10 interrupt
machinery end to end, including across a process restart and across two processes racing the
same due thread (#303).
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentdeck.authoring.timers import TIMER_TYPE
from agentdeck.runtime.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _memory_checkpointer(monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_past_timer_is_due_and_completes_after_tick(app_project_timers):
    app = app_project_timers

    async def _scenario():
        async with app:
            paused = await app.run("PastTimerFlow", {}, session_id="t-past")
            due = await app._due_resumes()
            finished = await app._tick()
        return paused, due, finished

    paused, due, finished = asyncio.run(_scenario())

    assert paused["type"] == "interrupt"
    assert paused["payload"]["type"] == TIMER_TYPE
    # the memory saver is cached per process (see test_workflow_interrupts.py), so scope to this thread
    assert [d for d in due if d["thread_id"] == "t-past"] == [paused]
    assert any(f.get("woke_at") for f in finished if isinstance(f, dict))  # _tick() resumed it


def test_future_timer_is_pending_but_not_due(app_project_timers):
    app = app_project_timers

    async def _scenario():
        async with app:
            paused = await app.run("FutureTimerFlow", {}, session_id="t-future")
            pending = await app.runs.pending()
            due = await app._due_resumes()
        return paused, pending, due

    paused, pending, due = asyncio.run(_scenario())

    assert [p.thread_id for p in pending if p.invocable == "FutureTimerFlow" and p.thread_id == "t-future"] == [
        "t-future"
    ]
    assert [d for d in due if d["thread_id"] == "t-future"] == []


def test_sleep_until_rejects_naive_datetime(app_project_timers):
    app = app_project_timers

    async def _scenario():
        async with app:
            return await app.run("NaiveTimerFlow", {}, session_id="t-naive")

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(_scenario())


def test_due_resumes_rejects_naive_now(app_project_timers):
    app = app_project_timers
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(app._due_resumes(datetime.now()))  # noqa: DTZ005 - deliberately naive


@pytest.fixture
def app_project_timers(tmp_path, monkeypatch):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=365)).isoformat()

    root = tmp_path / ".agentdeck" / "workflows"
    for name, module_src in [
        (
            "past_timer_flow",
            _TIMER_WORKFLOW_PY.format(
                cls="PastTimerFlow", var="past_timer_flow", when=f'datetime.fromisoformat("{past}")'
            ),
        ),
        (
            "future_timer_flow",
            _TIMER_WORKFLOW_PY.format(
                cls="FutureTimerFlow", var="future_timer_flow", when=f'datetime.fromisoformat("{future}")'
            ),
        ),
        (
            "naive_timer_flow",
            _TIMER_WORKFLOW_PY.format(cls="NaiveTimerFlow", var="naive_timer_flow", when="datetime(2030, 1, 1)"),
        ),
    ]:
        bundle = root / name
        bundle.mkdir(parents=True)
        (bundle / "workflow.py").write_text(textwrap.dedent(module_src))

    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck.deck import Deck

    return Deck.from_project()


_TIMER_WORKFLOW_PY = """
from datetime import datetime
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.authoring import Workflow, sleep_until

class State(BaseModel):
    woke_at: str = ""

def _build_graph():
    g = StateGraph(State)
    # distinct node name per workflow: foreign-graph replay only sees interrupts on nodes that graph has too
    g.add_node("{cls}_wait", lambda s: {{"woke_at": str(sleep_until({when}))}})
    g.set_entry_point("{cls}_wait")
    g.add_edge("{cls}_wait", END)
    return g

{var} = Workflow(name="{cls}", state=State, durable=True, graph=_build_graph)
"""


_RESTART_WORKFLOW_PY = """
from datetime import datetime, timedelta, timezone
from langgraph.graph import END, StateGraph
from pydantic import BaseModel
from agentdeck.authoring import Workflow, sleep_until

class State(BaseModel):
    woke_at: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("wait", lambda s: {"woke_at": str(sleep_until(datetime.now(timezone.utc) - timedelta(days=1)))})
    g.set_entry_point("wait")
    g.add_edge("wait", END)
    return g

timer_flow = Workflow(name="TimerFlow", state=State, durable=True, graph=_build_graph)
"""

_RESTART_SCRIPT = """
import asyncio, json, sys
from agentdeck.deck import Deck

async def main():
    async with Deck.from_project() as deck:
        if sys.argv[1] == "start":
            return await deck.run("TimerFlow", {}, session_id="restart-timer")
        due = await deck._due_resumes()
        resumed = await deck._tick()
        return {"due": due, "resumed": resumed}

print(json.dumps(asyncio.run(main()), default=str))
"""


def _run_script(arg: str, cwd: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_RESTART_SCRIPT), arg],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_tick_survives_a_process_restart(tmp_path):
    """A different process reads the timer inbox off the sqlite file and Deck._tick() resumes
    it — the acceptance test for #22, in miniature."""
    import json

    bundle = tmp_path / ".agentdeck" / "workflows" / "timer_flow"
    bundle.mkdir(parents=True)
    (bundle / "workflow.py").write_text(textwrap.dedent(_RESTART_WORKFLOW_PY))
    env = {
        **os.environ,
        "AGENTDECK_CHECKPOINT": f"sqlite://{tmp_path / 'checkpoints.sqlite3'}",
    }

    paused = json.loads(_run_script("start", str(tmp_path), env))
    finished = json.loads(_run_script("resume", str(tmp_path), env))

    assert paused["type"] == "interrupt"
    assert paused["payload"]["type"] == TIMER_TYPE
    assert finished["due"] == [paused]
    resumed_woke_at = datetime.fromisoformat(finished["resumed"][0]["woke_at"])
    assert resumed_woke_at == datetime.fromisoformat(paused["payload"]["wake_at"])


# --- two decks sweeping the same due thread must not both resume it (#303) ------------------

_RACE_SCRIPT = """
import asyncio, json, sys, time
from pathlib import Path
from agentdeck.deck import Deck

async def main():
    root = Path(sys.argv[1])
    tag = sys.argv[2]
    (root / f"ready-{tag}").touch()
    deadline = time.monotonic() + 20.0
    while not (root / "go").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("the 'go' file never appeared")
        time.sleep(0.005)
    async with Deck.from_project() as deck:
        return await deck._tick()

print(json.dumps(asyncio.run(main()), default=str))
"""


def test_two_decks_racing_tick_do_not_double_resume_the_same_thread(tmp_path):
    """Two processes hold the same durable checkpoint and event store, both find the same past-
    due thread, and both call ``_tick()`` at the same instant. ``_tick()`` resumes through the
    Runtime, whose claim on the WAITING_ANSWER -> RUNNING transition is a conditional append — so
    exactly one of the two calls may resume the thread and the other must find nothing left to
    do, not resume the graph a second time. A logged run is what puts ``_tick()`` on that claimed
    path at all (see ``_tick``'s own docstring), so the event store has to be durable and shared
    across all three processes here, not the default in-process ``memory://``.
    """
    import json

    bundle = tmp_path / ".agentdeck" / "workflows" / "timer_flow"
    bundle.mkdir(parents=True)
    (bundle / "workflow.py").write_text(textwrap.dedent(_RESTART_WORKFLOW_PY))
    env = {
        **os.environ,
        "AGENTDECK_CHECKPOINT": f"sqlite://{tmp_path / 'checkpoints.sqlite3'}",
        "AGENTDECK_EVENTS": f"sqlite://{tmp_path / 'events.sqlite3'}",
    }

    _run_script("start", str(tmp_path), env)

    tags = ("a", "b")
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(_RACE_SCRIPT), str(tmp_path), tag],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(tmp_path),
            env=env,
        )
        for tag in tags
    ]
    deadline = time.monotonic() + 20.0
    while not all((tmp_path / f"ready-{tag}").exists() for tag in tags):
        assert time.monotonic() < deadline, "one of the racing processes never reached its gate"
        time.sleep(0.005)
    (tmp_path / "go").touch()

    resumed_by_tag = {}
    for tag, racer in zip(tags, racers, strict=True):
        stdout, stderr = racer.communicate(timeout=30)
        assert racer.returncode == 0, stderr
        resumed_by_tag[tag] = json.loads(stdout.strip())

    resumed_lengths = [len(resumed) for resumed in resumed_by_tag.values()]
    assert sum(resumed_lengths) == 1, resumed_by_tag
