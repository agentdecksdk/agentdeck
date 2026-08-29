"""``agentdeck runs signal``: the one caller-facing surface that reaches a ``ControlPort``
directly, with no ``Runtime`` in the way. Its argument is the canonical id itself  -  no
namespace, no resolution step (#324)  -  so what it writes under is exactly what the caller typed.
"""

from __future__ import annotations

import asyncio
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
from agentdeck.core.events import RunCompleted, TextDelta, Usage
from agentdeck.runtime.service import Runtime


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
    project = tmp_path / ".agentdeck" / "agents" / "greeter"
    project.mkdir(parents=True)
    (project / "agent.py").write_text(
        textwrap.dedent("""
        from agentdeck.authoring import Agent

        it = Agent(name="Greeter", instructions="Greet the user.")
        """)
    )
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
