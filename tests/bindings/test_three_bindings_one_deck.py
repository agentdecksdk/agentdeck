"""SPI v1 freeze evidence (#554): the three bindings v6.0.0 ships (`Native.http()`,
`AGUI.http()`, `Terminal.stdio()`) share one Deck. `test_contract.py` already proves this
generically against the fixture plugin; this file proves it against the shipped set.
"""

from __future__ import annotations

import inspect
import io
import json

import pytest
from starlette.testclient import TestClient

from agentdeck import WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.bindings.agui import AGUI
from agentdeck.bindings.native import Native
from agentdeck.bindings.terminal import Terminal
from agentdeck.deck import Deck
from agentdeck.testing import ScriptedModel, patch_model


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def _two_asks(ctx: WorkflowCtx, topic: str) -> str:
    color = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    size = await ctx.ask(f"pick a size for {topic}?", options=["small", "large"])
    return f"{topic}:{color}:{size}"


def _deck() -> Deck:
    return Deck(
        agents=[Agent(name="Greeter", instructions="Greet the user.")],
        workflows=[workflow(_two_asks, name="Survey")],
    )


def _body(**overrides) -> dict:
    body = {
        "threadId": "t1",
        "runId": "r1",
        "tools": [],
        "context": [],
        "forwardedProps": None,
        "messages": [{"id": "m1", "role": "user", "content": "kites"}],
    }
    body.update(overrides)
    return body


def _events_from(response) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


def _outcome(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "RUN_FINISHED")["outcome"]


async def test_a_run_started_over_agui_is_answered_over_native_and_agui_receives_the_resumed_segment(
    no_project,
):
    """One Run, two bindings: Native drives a run it never started past its first interrupt,
    and AG-UI's own second POST resumes the second interrupt on its own stream (exposure.md:
    "a run started over A2A is visible over native HTTP ... That is a contract test.").
    """
    deck = _deck()
    async with deck:
        exposure = deck.expose(Native.http(), AGUI.http("/agui", target="Survey"))
        with TestClient(exposure.asgi()) as client:
            started = _events_from(client.post("/agui", json=_body()))
            interrupt_1 = _outcome(started)["interrupts"][0]

            listed = client.get("/runs").json()
            assert len(listed) == 1
            run_id = listed[0]["run_id"]
            assert listed[0]["session_id"] == "t1"  # threadId to session_id, ruling 8

            native_at_i1 = _events_from(client.get(f"/runs/{run_id}/events"))
            assert native_at_i1[-1]["kind"] == "run.interrupted"
            assert native_at_i1[-1]["run_id"] == run_id
            i1_seq = native_at_i1[-1]["seq"]
            assert native_at_i1[-1]["payload"]["interrupt_id"] == interrupt_1["id"]  # ruling 44

            answered = client.post(f"/runs/{run_id}/answer", json={"value": "red"})
            assert answered.status_code == 200

            resumed = _events_from(client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(i1_seq)}))
            assert resumed[-1]["kind"] == "run.interrupted"  # ask 2; Native never answers this one
            i2_id = resumed[-1]["payload"]["interrupt_id"]
            assert i2_id != interrupt_1["id"]

            # AG-UI's own wire vocabulary never crosses into Native's canonical event payloads.
            raw = client.get(f"/runs/{run_id}/events").text
            assert "RUN_STARTED" not in raw
            assert "TEXT_MESSAGE_START" not in raw

            resume_body = _body(runId="r2", resume=[{"interruptId": i2_id, "status": "resolved", "payload": "large"}])
            resumed_agui = _events_from(client.post("/agui", json=resume_body))

    assert _outcome(resumed_agui) == {"type": "success"}


async def test_a_terminal_run_is_visible_over_native_but_terminal_cannot_observe_others(no_project):
    """Terminal is one session per process (`bindings.md`): `StdioEndpoint.run` takes no run
    identity, so it can drive only the run it starts itself, never one another binding started.
    """
    deck = _deck()
    model = ScriptedModel(deltas=("hi",))
    async with deck:
        exposure = deck.expose(Native.http())
        with TestClient(exposure.asgi()) as client:
            stdout = io.StringIO()
            terminal = Terminal.stdio(target="Greeter", stdin=io.StringIO("hello\n"), stdout=stdout)
            endpoint = terminal.build(DeckGateway(deck))
            with patch_model(model):
                await endpoint.run()

            assert "-- run.completed --" in stdout.getvalue()

            listed = client.get("/runs").json()
            assert len(listed) == 1
            assert listed[0]["status"] == "completed"

    assert inspect.signature(endpoint.run).parameters == {}
