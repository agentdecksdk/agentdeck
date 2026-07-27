"""Durable timer waits (issue #22): sleep_until pauses a durable workflow until a wall-clock
moment; App.tick() resumes threads whose wake time has passed. Reuses the #10 interrupt
machinery end to end, including across a process restart.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta

import pytest

from agentdeck.runtime.settings import reset_settings_cache
from agentdeck.workflows.timers import TIMER_TYPE


@pytest.fixture(autouse=True)
def _memory_checkpointer(monkeypatch):
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_past_timer_is_due_and_completes_after_tick(app_project_timers):
    app = app_project_timers

    async def _scenario():
        paused = await app.run_workflow("PastTimerFlow", {}, thread_id="t-past")
        due = await app.due_resumes()
        finished = await app.tick()
        return paused, due, finished

    paused, due, finished = asyncio.run(_scenario())

    assert paused["type"] == "interrupt"
    assert paused["payload"]["type"] == TIMER_TYPE
    # the memory saver is cached per process (see test_workflow_interrupts.py), so scope to this thread
    assert [d for d in due if d["thread_id"] == "t-past"] == [paused]
    assert any(f.get("woke_at") for f in finished if isinstance(f, dict))  # tick() resumed it


def test_future_timer_is_pending_but_not_due(app_project_timers):
    app = app_project_timers

    async def _scenario():
        paused = await app.run_workflow("FutureTimerFlow", {}, thread_id="t-future")
        pending = await app.pending_interrupts("FutureTimerFlow")
        due = await app.due_resumes()
        return paused, pending, due

    paused, pending, due = asyncio.run(_scenario())

    assert [p["thread_id"] for p in pending if p["thread_id"] == "t-future"] == ["t-future"]
    assert [d for d in due if d["thread_id"] == "t-future"] == []


def test_sleep_until_rejects_naive_datetime(app_project_timers):
    app = app_project_timers

    async def _scenario():
        return await app.run_workflow("NaiveTimerFlow", {}, thread_id="t-naive")

    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(_scenario())


def test_due_resumes_rejects_naive_now(app_project_timers):
    app = app_project_timers
    with pytest.raises(ValueError, match="timezone-aware"):
        asyncio.run(app.due_resumes(datetime.now()))  # noqa: DTZ005 - deliberately naive


@pytest.fixture
def app_project_timers(tmp_path, monkeypatch):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=365)).isoformat()

    root = tmp_path / ".agentdeck" / "workflows"
    for name, module_src in [
        ("past_timer_flow", _TIMER_WORKFLOW_PY.format(cls="PastTimerFlow", when=f'datetime.fromisoformat("{past}")')),
        (
            "future_timer_flow",
            _TIMER_WORKFLOW_PY.format(cls="FutureTimerFlow", when=f'datetime.fromisoformat("{future}")'),
        ),
        ("naive_timer_flow", _TIMER_WORKFLOW_PY.format(cls="NaiveTimerFlow", when="datetime(2030, 1, 1)")),
    ]:
        bundle = root / name
        bundle.mkdir(parents=True)
        (bundle / "workflow.py").write_text(textwrap.dedent(module_src))

    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]
    from agentdeck import App

    return App()


_TIMER_WORKFLOW_PY = """
from datetime import datetime
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, sleep_until

class State(BaseModel):
    woke_at: str = ""

class {cls}(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        # distinct node name per workflow: foreign-graph replay only sees interrupts on nodes that graph has too
        g.add_node("{cls}_wait", lambda s: {{"woke_at": str(sleep_until({when}))}})
        g.set_entry_point("{cls}_wait")
        g.add_edge("{cls}_wait", END)
        return g
"""


_RESTART_WORKFLOW_PY = """
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, sleep_until

class State(BaseModel):
    woke_at: str = ""

class TimerFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("wait", lambda s: {"woke_at": str(sleep_until(datetime.now(timezone.utc) - timedelta(days=1)))})
        g.set_entry_point("wait")
        g.add_edge("wait", END)
        return g
"""

_RESTART_SCRIPT = """
import asyncio, json, sys
from agentdeck import App

async def main():
    app = App()
    if sys.argv[1] == "start":
        return await app.run_workflow("TimerFlow", {}, thread_id="restart-timer")
    due = await app.due_resumes()
    resumed = await app.tick()
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
    """A different process reads the timer inbox off the sqlite file and App.tick() resumes
    it — the acceptance test for #22, in miniature."""
    import json

    pytest.importorskip("langgraph.checkpoint.sqlite", reason="needs the [durability] extra")
    bundle = tmp_path / ".agentdeck" / "workflows" / "timer_flow"
    bundle.mkdir(parents=True)
    (bundle / "workflow.py").write_text(textwrap.dedent(_RESTART_WORKFLOW_PY))
    env = {
        **os.environ,
        "AGENTDECK_CHECKPOINT_BACKEND": "sqlite",
        "AGENTDECK_CHECKPOINT_URL": str(tmp_path / "checkpoints.sqlite3"),
    }

    paused = json.loads(_run_script("start", str(tmp_path), env))
    finished = json.loads(_run_script("resume", str(tmp_path), env))

    assert paused["type"] == "interrupt"
    assert paused["payload"]["type"] == TIMER_TYPE
    assert finished["due"] == [paused]
    resumed_woke_at = datetime.fromisoformat(finished["resumed"][0]["woke_at"])
    assert resumed_woke_at == datetime.fromisoformat(paused["payload"]["wake_at"])
