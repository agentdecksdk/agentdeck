"""``agentdeck runs signal`` (#315 review): the one caller-facing surface that reaches a
``ControlPort`` directly, with no ``Runtime`` and no ``RunContext`` of its own building one for
it — so it is also the one surface that could bypass the validation every other entry point gets
for free by going through :class:`~agentdeck.core.context.RunContext`.
"""

from __future__ import annotations

import asyncio

import pytest

from agentdeck import cli
from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.control import ControlSignal, Signal
from agentdeck.core.events import RunCompleted, TextDelta, Usage
from agentdeck.runtime.service import Runtime


def _poll(db_path: object, ref: str) -> ControlSignal | None:
    async def _read() -> ControlSignal | None:
        control = SqliteControlPort(db_path)
        try:
            return await control.poll(ref)
        finally:
            control.close()

    return asyncio.run(_read())


def test_a_signal_lands_under_the_bare_run_id_it_was_given(tmp_path) -> None:
    """The compatibility keystone in practice: this CLI has no ``--namespace``, so
    ``encode(None, run_id) == run_id`` means the ref it writes under is byte-identical to what
    the caller typed — zero behavior change for every real invocation."""
    db_path = tmp_path / "control.sqlite3"

    assert cli.main(["runs", "signal", "order-1234", "cancel", "--control-db", str(db_path)]) == 0

    assert _poll(db_path, "order-1234") == ControlSignal(verb=Signal.CANCEL, reason=None)


def test_a_run_id_shaped_like_a_namespaced_ref_is_refused(tmp_path) -> None:
    """This used to call ``SqliteControlPort.signal()`` directly, with no ``RunContext`` and no
    validation at all — so a caller could type a ``run_id`` shaped exactly like
    ``encode(namespace, run_id)`` and hijack that namespace's live run: its ``Gate`` polls under
    precisely that ref. Routed through ``RunContext`` now, so the ``adr:`` reservation fires
    here too, and the forged signal is refused before it ever reaches the port."""
    db_path = tmp_path / "control.sqlite3"

    with pytest.raises(ValueError, match="adr:"):
        cli.main(["runs", "signal", "adr:acme:order-1234", "cancel", "--control-db", str(db_path)])

    # Refused before any write: the file holds no such row for a later, legitimate namespaced
    # caller to collide with either.
    assert _poll(db_path, "adr:acme:order-1234") is None


async def test_an_unnamespaced_cli_signal_does_not_reach_a_namespaced_run() -> None:
    """The isolation boundary this CLI now sits behind: it can only ever address a ref
    byte-identical to a bare ``run_id`` (it has no ``--namespace`` flag and, per
    docs/design/run-identity.md, never will), so a namespaced run sharing that literal
    ``run_id`` is untouched — the same guarantee ``test_uc3_cross_process_cancel`` now relies on
    by running unnamespaced on both sides."""
    control = MemoryControlPort()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="hi"),
        RunCompleted(output=coerce_input("hi"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)

    await control.signal("order-1234", Signal.CANCEL)  # what the CLI would have written

    acme = [event async for event in runtime.run("Chatty", coerce_input("hi"), run_id="order-1234", namespace="acme")]

    assert [event.kind for event in acme][-1] == "run.completed"  # the namespaced run, untouched
