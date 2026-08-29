"""``ProtocolGateway``: delegation, failure mapping, ``targets()`` and ``capabilities``.

No binding exists yet (#548/#552 add the first), so these test the facade directly against a
plain ``Deck``, the same way ``tests/test_serve.py`` tests ``serve.py``'s handlers directly.
"""

import asyncio
from typing import Any

import pytest

from agentdeck import WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings import Capabilities, GatewayError, GatewayFailureCode, ProtocolGateway, TargetInfo
from agentdeck.bindings.gateway import _map_failure
from agentdeck.deck import Deck, Run
from agentdeck.errors import DuplicateKeyError, NotFoundError, RunStateError, SessionBusyError, UnsupportedControlError
from agentdeck.runtime.settings import reset_settings_cache
from agentdeck.testing import ScriptedModel, patch_model


def _greeter(name: str = "Greeter", **kwargs: Any) -> Agent:
    return Agent(name=name, instructions="Greet the user.", **kwargs)


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def scripted():
    model = ScriptedModel(deltas=["hi"])
    with patch_model(model):
        yield model


# --- delegation ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_get_list_delegate_to_deck_runs_and_return_real_runs(no_project, scripted):
    deck = Deck(agents=[_greeter()])
    async with deck:
        gateway = ProtocolGateway(deck)

        started = await gateway.start("Greeter", "hi", session_id="s1")
        assert isinstance(started, Run)

        fetched = await gateway.get_run(started.id, namespace=started.namespace)
        assert fetched.id == started.id

        listed = await gateway.list_runs()
        assert [run.id for run in listed] == [started.id]


# --- failure mapping -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_maps_an_unknown_target_to_not_found(no_project):
    deck = Deck(agents=[_greeter()])
    async with deck:
        gateway = ProtocolGateway(deck)
        with pytest.raises(GatewayError) as excinfo:
            await gateway.start("NoSuchAgent", "hi")

    assert excinfo.value.code is GatewayFailureCode.NOT_FOUND
    assert "NoSuchAgent" in excinfo.value.message
    assert isinstance(excinfo.value.cause, NotFoundError)


@pytest.mark.asyncio
async def test_start_maps_bad_input_to_invalid_input(no_project):
    deck = Deck(agents=[_greeter()])
    async with deck:
        gateway = ProtocolGateway(deck)
        with pytest.raises(GatewayError) as excinfo:
            await gateway.start("Greeter", 123)  # an agent only ever takes str/content blocks

    assert excinfo.value.code is GatewayFailureCode.INVALID_INPUT
    assert isinstance(excinfo.value.cause, TypeError)


@pytest.mark.asyncio
async def test_start_maps_a_busy_session_to_busy(no_project):
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("Hel", "lo"), hold=hold)
    deck = Deck(agents=[_greeter()])
    with patch_model(model):
        async with deck:
            gateway = ProtocolGateway(deck)
            running = await gateway.start("Greeter", "hello", session_id="s-busy")
            await model.holding.wait()
            with pytest.raises(GatewayError) as excinfo:
                await gateway.start("Greeter", "hello again", session_id="s-busy")
            hold.set()
            await running

    assert excinfo.value.code is GatewayFailureCode.BUSY
    assert isinstance(excinfo.value.cause, SessionBusyError)


@pytest.mark.asyncio
async def test_start_maps_a_reused_key_to_conflict(no_project, scripted):
    deck = Deck(agents=[_greeter()])
    async with deck:
        gateway = ProtocolGateway(deck)
        await gateway.start("Greeter", "hi", session_id="s1", key="k1")
        with pytest.raises(GatewayError) as excinfo:
            await gateway.start("Greeter", "hi again", session_id="s2", key="k1")

    assert excinfo.value.code is GatewayFailureCode.CONFLICT
    assert isinstance(excinfo.value.cause, DuplicateKeyError)


@pytest.mark.asyncio
async def test_get_run_maps_an_unknown_id_to_not_found(no_project):
    deck = Deck(agents=[_greeter()])
    async with deck:
        gateway = ProtocolGateway(deck)
        with pytest.raises(GatewayError) as excinfo:
            await gateway.get_run("no-such-run")

    assert excinfo.value.code is GatewayFailureCode.NOT_FOUND


def test_map_failure_maps_run_state_error_to_conflict():
    """``RunStateError`` never reaches ``start``/``get``/``list`` in practice (it is a
    ``Run``-method refusal), so its mapping is exercised directly on the mapper."""
    mapped = _map_failure(RunStateError("run 'r1' cannot cancel: already over"))
    assert mapped.code is GatewayFailureCode.CONFLICT


def test_map_failure_maps_unsupported_control_error_to_unsupported():
    mapped = _map_failure(UnsupportedControlError("no control backend is configured"))
    assert mapped.code is GatewayFailureCode.UNSUPPORTED


def test_map_failure_never_echoes_the_cause_for_internal():
    cause = RuntimeError("db dsn=postgres://user:hunter2@host/db")
    mapped = _map_failure(cause)

    assert mapped.code is GatewayFailureCode.INTERNAL
    assert mapped.message == "internal error"
    assert "hunter2" not in mapped.message
    assert mapped.cause is cause


# --- targets ---------------------------------------------------------------------------------


def test_targets_lists_agents_and_workflows_with_description_and_schema(no_project):
    async def two_numbers(ctx: WorkflowCtx, a: int, b: int) -> int:
        return a + b

    deck = Deck(
        agents=[_greeter(handoff_description="Greets whoever is talking.")],
        workflows=[workflow(two_numbers, name="Add")],
    )
    gateway = ProtocolGateway(deck)

    targets = {target.name: target for target in gateway.targets()}

    agent_target = targets["Greeter"]
    assert agent_target == TargetInfo(
        name="Greeter", kind="agent", description="Greets whoever is talking.", input_schema=None
    )

    workflow_target = targets["Add"]
    assert workflow_target.kind == "workflow"
    assert workflow_target.input_schema is not None
    assert set(workflow_target.input_schema["required"]) == {"a", "b"}
    assert workflow_target.input_schema["properties"]["a"]["type"] == "integer"


def test_targets_input_schema_is_none_for_a_workflow_with_no_parameters(no_project):
    async def ping(ctx: WorkflowCtx) -> str:
        return "pong"

    deck = Deck(agents=[], workflows=[workflow(ping, name="Ping")])
    gateway = ProtocolGateway(deck)

    (target,) = gateway.targets()
    assert target.input_schema is None


# --- capabilities ------------------------------------------------------------------------------


def test_capabilities_are_false_on_the_default_memory_backends(no_project, monkeypatch):
    monkeypatch.delenv("AGENTDECK_EVENTS", raising=False)
    monkeypatch.delenv("AGENTDECK_CONTROL", raising=False)
    reset_settings_cache()
    try:
        gateway = ProtocolGateway(Deck(agents=[_greeter()]))
        assert gateway.capabilities == Capabilities(control=False, durable=False)
    finally:
        reset_settings_cache()


def test_capabilities_are_true_once_a_real_backend_is_configured(no_project, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTDECK_EVENTS", f"sqlite:///{tmp_path / 'events.db'}")
    monkeypatch.setenv("AGENTDECK_CONTROL", f"sqlite:///{tmp_path / 'control.db'}")
    reset_settings_cache()
    try:
        gateway = ProtocolGateway(Deck(agents=[_greeter()]))
        assert gateway.capabilities == Capabilities(control=True, durable=True)
    finally:
        reset_settings_cache()
