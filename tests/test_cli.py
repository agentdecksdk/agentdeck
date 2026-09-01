"""``agentdeck runs signal``: the one caller-facing surface that reaches a ``ControlPort``
directly, with no ``Runtime`` in the way. Its argument is the canonical id itself  -  no
namespace, no resolution step (#324)  -  so what it writes under is exactly what the caller typed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import textwrap

from agentdeck import cli
from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.executors.stub import StubExecutor, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.control import ControlSignal, Signal
from agentdeck.core.events import Event, RunCompleted, TextDelta, Usage
from agentdeck.core.status import RunStatus, status_of
from agentdeck.runtime.service import Runtime

_AGENT_PY = """
from agentdeck.authoring import Agent

it = Agent(name="Greeter", instructions="Greet the user.")
"""

_SLOW_WORKFLOW_PY = """
import asyncio

from agentdeck import WorkflowCtx, workflow


@workflow(name="Slow")
async def slow(ctx: WorkflowCtx, text: str) -> str:
    await asyncio.sleep(30)
    return text
"""


def _write_one_agent_project(tmp_path) -> None:
    project = tmp_path / ".agentdeck" / "agents" / "greeter"
    project.mkdir(parents=True)
    (project / "agent.py").write_text(textwrap.dedent(_AGENT_PY))


def _write_two_agent_project(tmp_path) -> None:
    for name in ("Alpha", "Bravo"):
        project = tmp_path / ".agentdeck" / "agents" / name.lower()
        project.mkdir(parents=True)
        (project / "agent.py").write_text(
            f'from agentdeck.authoring import Agent\n\nit = Agent(name="{name}", instructions=".")\n'
        )


def _write_slow_workflow_project(tmp_path) -> None:
    project = tmp_path / ".agentdeck" / "workflows" / "slow"
    project.mkdir(parents=True)
    (project / "workflow.py").write_text(textwrap.dedent(_SLOW_WORKFLOW_PY))


def _poll(db_path: object, id: str) -> ControlSignal | None:
    async def _read() -> ControlSignal | None:
        control = SqliteControlPort(db_path)
        try:
            return await control.poll(id)
        finally:
            control.close()

    return asyncio.run(_read())


def test_a_signal_lands_under_the_bare_run_id_it_was_given(tmp_path) -> None:
    """The argument is the canonical id directly, with no resolution step: what the caller
    typed is exactly what the port is signalled with."""
    db_path = tmp_path / "control.sqlite3"

    assert cli.main(["runs", "signal", "order-1234", "cancel", "--control-db", str(db_path)]) == 0

    assert _poll(db_path, "order-1234") == ControlSignal(verb=Signal.CANCEL, reason=None)


async def test_an_unnamespaced_cli_signal_does_not_reach_a_namespaced_run() -> None:
    """The isolation boundary this CLI now sits behind: it addresses whatever literal string
    its argument names, with no namespace and no resolution step (it has no ``--namespace``
    flag and, per docs/design/run-identity.md, never will). A run started with ``key=`` in a
    real namespace mints its own id regardless of that key (#324), so signalling the literal
    a CLI caller might type can no longer land on it even by coincidence  -  the way a
    caller-chosen ``run_id`` used to be able to before the split."""
    control = MemoryControlPort()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="hi"),
        RunCompleted(output=coerce_input("hi"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubExecutor()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)

    await control.signal("order-1234", Signal.CANCEL)  # what the CLI would have written

    acme = [event async for event in runtime.run("Chatty", coerce_input("hi"), key="order-1234", namespace="acme")]

    assert acme[0].run_id != "order-1234"  # minted, never the key a caller happened to pass
    assert [event.kind for event in acme][-1] == "run.completed"  # the namespaced run, untouched


def test_agentdeck_chat_starts_with_no_http_dependency_importable(tmp_path) -> None:
    """`Terminal.stdio()` is a stdio binding: `agentdeck chat` must reach `Exposure.serve()`
    without importing uvicorn or fastapi, the `[serve]` extra, anywhere on that path
    (rulings.md 35). `httpx` is not part of this: it is a base dependency regardless of any
    binding (`openai`'s own SDK imports it, and `agentdeck/deck.py` imports that unconditionally),
    so blocking it fails before `import agentdeck` and would prove nothing about this path.

    A fresh subprocess with each set to `None` in `sys.modules` before any import, same
    rationale as `test_composition.py`'s identical redis probe: this process already has both
    imported, and `sys.modules` cannot unsee that.
    """
    _write_one_agent_project(tmp_path)
    probe = textwrap.dedent("""
        import sys
        sys.modules["uvicorn"] = None
        sys.modules["fastapi"] = None
        from agentdeck.cli import main
        raise SystemExit(main(["chat"]))
        """)

    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, input="", capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 0, done.stderr


def test_chat_names_the_targets_when_the_deck_holds_more_than_one(tmp_path) -> None:
    """`agentdeck chat` with nothing to default to fails at `expose()` naming what is available,
    before any prompt."""
    _write_two_agent_project(tmp_path)
    probe = textwrap.dedent("""
        from agentdeck.cli import main
        raise SystemExit(main(["chat"]))
        """)

    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, input="", capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 2, done.stderr
    assert "Alpha" in done.stderr and "Bravo" in done.stderr
    assert "Traceback" not in done.stderr  # a usage mistake reads as one


def test_chat_refuses_an_unknown_target_without_a_traceback(tmp_path) -> None:
    """Verified against a real pty as well: `agentdeck chat Nope` prints
    `agentdeck chat: no target named 'Nope' in this deck. Available: [...]` and exits 2.
    """
    _write_one_agent_project(tmp_path)
    probe = textwrap.dedent("""
        from agentdeck.cli import main
        raise SystemExit(main(["chat", "Nope"]))
        """)

    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, input="", capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 2, done.stderr
    assert "Nope" in done.stderr
    assert "Traceback" not in done.stderr


def test_ctrl_c_mid_run_cancels_the_run_and_exits_cleanly(tmp_path) -> None:
    """Ctrl-C while a run is in flight: a real SIGINT to a real subprocess, self-sent once a
    slow workflow's run has actually started (a durable sqlite log, read after exit, is how the
    run's own status is checked  -  the subprocess is gone by then). `asyncio.Runner` converts
    the resulting `CancelledError` into `KeyboardInterrupt` (rulings.md 35, #549 review).

    Drives the CLI itself: `agentdeck chat Slow` names the target, so a workflow-only project
    needs no direct `Deck` call. Exit is ordinary too: the binding's stdin reader is a daemon
    thread, so nothing is left to join and `os._exit` is gone.
    """
    _write_slow_workflow_project(tmp_path)
    db_path = tmp_path / "events.sqlite3"
    probe = textwrap.dedent(f"""
        import asyncio, os, signal, sqlite3, threading, time

        def _watch():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    conn = sqlite3.connect({str(db_path)!r})
                    rows = conn.execute("SELECT data FROM events").fetchall()
                    conn.close()
                    if any('"run.started"' in row[0] for row in rows):
                        break
                except sqlite3.OperationalError:
                    pass
                time.sleep(0.02)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=_watch, daemon=True).start()

        from agentdeck.cli import main

        raise SystemExit(main(["chat", "Slow"]))
        """)
    env = {**os.environ, "AGENTDECK_EVENTS": f"sqlite:///{db_path}"}

    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, input="go\n", capture_output=True, text=True, timeout=60, env=env
    )

    assert done.returncode == 0, done.stderr
    conn = sqlite3.connect(db_path)
    events = [Event.model_validate(json.loads(row[0])) for row in conn.execute("SELECT data FROM events ORDER BY id")]
    conn.close()
    assert status_of(events) == RunStatus.CANCELLED


def test_ctrl_c_idle_at_the_prompt_exits_cleanly_with_no_traceback(tmp_path) -> None:
    """Ctrl-C with no run in flight: a real SIGINT while blocked reading the `> ` prompt must
    exit 0 with nothing on stderr, not the raw `CancelledError` traceback and exit 1 the
    original SIGINT handler produced (review BLOCK on `agentdeck/cli.py:52`).
    """
    _write_one_agent_project(tmp_path)
    probe = textwrap.dedent("""
        import os, signal, threading, time

        from agentdeck.cli import main  # import first: the watcher's delay only has to cover

        def _watch():                   # reaching the blocked prompt read from here on
            time.sleep(1)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=_watch, daemon=True).start()
        raise SystemExit(main(["chat"]))
        """)

    proc = subprocess.Popen(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        stdin=subprocess.PIPE,  # left open and unfed: a real blocked prompt read, not EOF
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, stderr = proc.communicate(timeout=15)  # closes stdin itself once the process has exited

    assert proc.returncode == 0, stderr
    assert "Traceback" not in stderr, stderr
