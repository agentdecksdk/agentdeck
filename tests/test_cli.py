"""``agentdeck runs signal``: the one caller-facing surface that reaches a ``ControlPort``
directly, with no ``Runtime`` in the way. Its argument is the canonical id itself  -  no
namespace, no resolution step (#324)  -  so what it writes under is exactly what the caller typed.
"""

from __future__ import annotations

import asyncio

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
