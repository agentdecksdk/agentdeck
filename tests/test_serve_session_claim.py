"""What a busy session looks like over HTTP: 409, before the stream starts.

The refusal has to be an answer, not a truncated body. A run is claimed on the first event of
an async generator, so an SSE route that hands the generator straight to ``StreamingResponse``
commits ``200`` and ``text/event-stream`` before the claim is even attempted, and the refusal
then reaches the client as a body that simply stops  -  which is exactly what a run producing no
events looks like. These tests pin the status, the message, and that the ordinary streaming
path did not lose or duplicate its opening event on the way to fixing that.
"""

from __future__ import annotations

import json

import httpx

from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import TextBlock
from agentdeck.core.context import RunContext
from agentdeck.core.events import (
    Event,
    MessageCompleted,
    RunCompleted,
    RunInterrupted,
    RunStarted,
    Usage,
)
from agentdeck.runtime.service import Runtime
from agentdeck.surfaces.serve.app import build_app

SESSION_ID = "s-busy"
AGENT = "Greeter"
HOLDER = "run-in-flight"
CHAT = f"/v2/invocables/{AGENT}/chat"


def _runtime() -> tuple[Runtime, MemoryEventStore]:
    spec = stub_spec(
        AGENT,
        MessageCompleted(message_id="m1", text="hi back"),
        RunCompleted(output=[TextBlock(text="hi back")], usage=Usage(input_tokens=1, output_tokens=1)),
    )
    store = MemoryEventStore()
    return Runtime([StubEngine()], store, {spec.name: spec}), store


def _reader() -> RunContext:
    return RunContext(run_id="reader", session_id=SESSION_ID)


def _holder() -> RunContext:
    """The run that already owns the session  -  its own context, because that is what files an
    event under its ``run_id`` now."""
    return RunContext(run_id=HOLDER, session_id=SESSION_ID)


async def _hold_the_session(store: MemoryEventStore) -> None:
    """Park a run in the session's log the way a process that is still working leaves one: open,
    and recent enough that no staleness window applies to it."""
    opening = RunStarted(
        invocable=AGENT,
        kind_of_invocable="agent",
        input=[TextBlock(text="the turn already running")],
    )
    waiting = RunInterrupted(interrupt_id="i1", reason="approval", payload={}, thread_id="t1")
    await store.append(SESSION_ID, [opening, waiting], _holder(), AGENT)


async def test_a_turn_on_a_busy_session_is_a_409_naming_the_run_that_holds_it() -> None:
    runtime, store = _runtime()
    await _hold_the_session(store)
    app = build_app(runtime)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(CHAT, json={"session_id": SESSION_ID, "message": "hi"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert SESSION_ID in detail, detail
    assert HOLDER in detail, detail
    # A refused turn is not a turn: the log still holds the one run it held before.
    assert {event.run_id for event in await store.read(SESSION_ID, _reader())} == {HOLDER}


async def test_an_empty_session_id_is_refused_rather_than_given_a_log_of_its_own() -> None:
    """``RunContext.log_key`` is ``session_id or run_id``, so ``""`` is not an error anywhere
    downstream  -  it quietly gives the turn a private log, and the caller's next message finds
    no history with nothing anywhere saying why. Caught at the boundary, where it is still a 422."""
    runtime, store = _runtime()
    app = build_app(runtime)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(CHAT, json={"session_id": "", "message": "hi"})

    assert response.status_code == 422
    assert await store.read(SESSION_ID, _reader()) == []  # and no run was played


async def test_a_turn_on_an_idle_session_still_streams_every_event_once() -> None:
    """The other half of pulling the first event before responding: the opening event must reach
    the client exactly once, and the rest of the run behind it."""
    runtime, store = _runtime()
    app = build_app(runtime)

    frames: list[Event] = []
    body = {"session_id": SESSION_ID, "message": "hi"}
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
        client.stream("POST", CHAT, json=body) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                frames.append(Event.model_validate(json.loads(line.removeprefix("data: "))))

    assert [event.kind for event in frames] == ["run.started", "message.completed", "run.completed"]
    assert frames == await store.read(SESSION_ID, _reader())
