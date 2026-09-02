"""`AGUI.http()` end to end through `Exposure.asgi()` (`docs/design/protocols/agui.md`): tests
1 to 19 of its own table. Adapter-level projections (8, 9, 18, and multimodal input) are unit
tests on the adapter functions directly, since `ScriptedModel` has no way to script reasoning or
an unknown kind; everything reachable over the wire uses `TestClient`, matching
`test_native_binding.py`'s own style.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from ag_ui.core import Event as AGUIWireEvent
from ag_ui.core import RunAgentInput
from pydantic import TypeAdapter
from starlette.testclient import TestClient

from agentdeck import Deck, ImageBlock, ResourceBlock, Run, TextBlock, WorkflowCtx, workflow
from agentdeck.adapters.bindings.agui.adapter import _AdapterState, _to_agentdeck_input, _to_agui_event
from agentdeck.adapters.bindings.agui.binding import _AGUIBinding
from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.bindings.agui import AGUI
from agentdeck.core.events import (
    Event,
    KnownPayload,
    MessageCompleted,
    RunCancelled,
    RunFailed,
    RunInterrupted,
    RunPaused,
    TextDelta,
    ThoughtDelta,
    UnknownEvent,
)
from agentdeck.core.status import RunStatus
from agentdeck.errors import ConfigError, InputError, RunStateError
from agentdeck.testing import ScriptedModel, patch_model

_WIRE_EVENT = TypeAdapter(AGUIWireEvent)
_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _lookup(city: str) -> str:
    return f"weather in {city}: sunny"


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


async def _echo(ctx: WorkflowCtx, input: str) -> str:
    return input.upper()


def _deck() -> Deck:
    return Deck(
        agents=[Agent(name="Greeter", instructions="Greet the user.", tools=[_lookup])],
        workflows=[workflow(_survey, name="Survey"), workflow(_echo, name="Echo")],
    )


def _client(deck: Deck, *bindings) -> TestClient:
    return TestClient(deck.expose(*(bindings or (AGUI.http("/agui"),))).asgi(), raise_server_exceptions=False)


def _body(**overrides) -> dict:
    body = {
        "threadId": "t1",
        "runId": "r1",
        "tools": [],
        "context": [],
        "forwardedProps": None,
        "messages": [{"id": "m1", "role": "user", "content": "hi"}],
    }
    body.update(overrides)
    return body


def _targeted(target: str, **overrides) -> dict:
    return _body(forwardedProps={"agentdeck": {"target": target}}, **overrides)


def _events_from_text(text: str) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in text.splitlines() if line.startswith("data: ")]


def _events_from(response) -> list[dict]:
    return _events_from_text(response.text)


async def _drain(response) -> list[dict]:
    """`response.body_iterator` consumed directly: a `_handle()` return, not one `TestClient`
    already read whole, which is what test 16 and this test need to hold a run open."""
    chunks = [chunk async for chunk in response.body_iterator]
    return _events_from_text("".join(chunks))


def _outcome(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "RUN_FINISHED")["outcome"]


def _event(payload: KnownPayload, *, run_id: str = "r", origin: str = "X") -> Event:
    return Event(
        kind=payload.kind, seq=0, run_id=run_id, session_id=None, namespace=None, origin=origin, ts=_TS, payload=payload
    )


class _FakeRequest:
    """Just enough of `starlette.requests.Request` for `_handle`: no ASGI transport, so a
    direct task cancellation is the disconnect signal, the same technique
    `test_serve_compat.py::test_a_disconnect_closes_its_run_in_the_log` uses.
    """

    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()
        self.headers: dict[str, str] = {}
        self.url = type("_URL", (), {"path": "/agui"})()

    async def body(self) -> bytes:
        return self._body

    async def json(self) -> dict:
        return json.loads(self._body)


def test_a_deck_with_both_an_agent_and_a_workflow_target_serves_both(no_project):
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        agent_events = _events_from(client.post("/agui", json=_targeted("Greeter")))
        workflow_events = _events_from(client.post("/agui", json=_targeted("Echo", threadId="t2", runId="r2")))

    assert _outcome(agent_events) == {"type": "success"}
    assert _outcome(workflow_events) == {"type": "success"}


def test_forwarded_props_target_routes_to_the_named_target(no_project):
    """The agent streams text; the workflow does not  -  distinguishing which one ran."""
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        to_agent = _events_from(client.post("/agui", json=_targeted("Greeter")))
        to_workflow = _events_from(client.post("/agui", json=_targeted("Echo", threadId="t2", runId="r2")))

    assert any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in to_agent)
    assert not any(e["type"] == "TEXT_MESSAGE_CONTENT" for e in to_workflow)


def test_a_pinned_binding_rejects_a_request_naming_a_different_target(no_project):
    with _client(_deck(), AGUI.http("/agui", target="Greeter")) as client:
        response = client.post("/agui", json=_targeted("Echo"))

    assert response.status_code == 422
    assert "pinned" in response.json()["detail"]


def test_a_pinned_unknown_target_fails_at_expose_not_on_request(no_project):
    """A pinned ``target=`` is deployment configuration (Terminal's own pattern): a typo is
    caught at ``deck.expose()``, never on a client's first request."""
    with pytest.raises(ConfigError, match="Typoooo"):
        _deck().expose(AGUI.http("/agui", target="Typoooo"))


def test_two_agui_bindings_coexist_in_one_exposure_using_name(no_project):
    model = ScriptedModel(deltas=("hi",))
    bindings = (
        AGUI.http("/a", target="Greeter", name="agui-a"),
        AGUI.http("/b", target="Greeter", name="agui-b"),
    )
    with patch_model(model), _client(_deck(), *bindings) as client:
        a = client.post("/a", json=_body())
        b = client.post("/b", json=_body(threadId="t2", runId="r2"))

    assert a.status_code == 200
    assert b.status_code == 200


async def test_thread_id_becomes_session_id(no_project):
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model), _client(deck, AGUI.http("/agui", target="Greeter")) as client:
        client.post("/agui", json=_body(threadId="thread-42"))
        runs = await deck.runs.list()

    assert [r.session_id for r in runs] == ["thread-42"]


async def test_a_second_user_turn_reuses_the_same_agentdeck_session(no_project):
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    with patch_model(model), _client(deck, AGUI.http("/agui", target="Greeter")) as client:
        first = client.post("/agui", json=_body())
        second = client.post("/agui", json=_body(runId="r2"))
        runs = await deck.runs.list()

    assert first.status_code == second.status_code == 200
    assert len(runs) == 2
    assert {r.session_id for r in runs} == {"t1"}


def test_text_deltas_produce_a_valid_agui_message_lifecycle(no_project):
    model = ScriptedModel(deltas=("Hel", "lo"))
    with patch_model(model), _client(_deck(), AGUI.http("/agui", target="Greeter")) as client:
        events = _events_from(client.post("/agui", json=_body()))

    kinds = [e["type"] for e in events if e["type"].startswith("TEXT_MESSAGE")]
    assert kinds == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]
    assert "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT") == "Hello"


def test_a_workflows_text_projects_identically_to_an_agents():
    """Both are one canonical event kind, projected through the same table; only ``origin``
    could possibly distinguish them, and the projection never reads it."""
    payload = MessageCompleted(message_id="m1", text="hi")
    from_agent = _event(payload, run_id="r1", origin="Greeter")
    from_workflow = _event(payload, run_id="r2", origin="Echo")

    agent_projection = [e.model_dump() for e in _to_agui_event(from_agent, _AdapterState(thread_id="t", run_id="r"))]
    workflow_projection = [
        e.model_dump() for e in _to_agui_event(from_workflow, _AdapterState(thread_id="t", run_id="r"))
    ]

    assert agent_projection == workflow_projection


def test_thought_delta_projects_to_the_reasoning_family():
    state = _AdapterState(thread_id="t", run_id="r")
    first = _event(ThoughtDelta(message_id="th1", text="hmm"))
    second = _event(ThoughtDelta(message_id="th1", text=" more"))
    boundary = _event(TextDelta(message_id="m1", text="hi"))

    opened = [e.type.value for e in _to_agui_event(first, state)]
    continued = [e.type.value for e in _to_agui_event(second, state)]
    closed = [e.type.value for e in _to_agui_event(boundary, state)]

    assert opened == ["REASONING_START", "REASONING_MESSAGE_START", "REASONING_MESSAGE_CONTENT"]
    assert continued == ["REASONING_MESSAGE_CONTENT"]
    assert closed == ["REASONING_MESSAGE_END", "REASONING_END", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]


def test_text_delta_then_run_failed_ends_the_text_message_first():
    state = _AdapterState(thread_id="t", run_id="r")
    _to_agui_event(_event(TextDelta(message_id="m1", text="hi")), state)

    closed = [
        e.type.value
        for e in _to_agui_event(_event(RunFailed(error_code="engine_error", message="boom", retryable=False)), state)
    ]

    assert closed == ["TEXT_MESSAGE_END", "RUN_ERROR"]


def test_text_delta_then_run_interrupted_ends_the_text_message_first():
    state = _AdapterState(thread_id="t", run_id="r")
    _to_agui_event(_event(TextDelta(message_id="m1", text="hi")), state)

    closed = [
        e.type.value
        for e in _to_agui_event(
            _event(RunInterrupted(interrupt_id="i1", reason="human", payload={"question": "ok?"})), state
        )
    ]

    assert closed == ["TEXT_MESSAGE_END", "RUN_FINISHED"]


def test_text_delta_then_run_cancelled_ends_the_text_message_first():
    state = _AdapterState(thread_id="t", run_id="r")
    _to_agui_event(_event(TextDelta(message_id="m1", text="hi")), state)

    closed = [e.type.value for e in _to_agui_event(_event(RunCancelled()), state)]

    assert closed == ["TEXT_MESSAGE_END", "RUN_ERROR"]


def test_run_paused_projects_to_a_custom_event_then_run_finished_success():
    """A pause is neither a question nor a failure: it closes the interaction with a
    CustomEvent carrying the reason, then RunFinishedSuccessOutcome (agui.md's own table)."""
    state = _AdapterState(thread_id="t", run_id="r")

    events = _to_agui_event(_event(RunPaused(reason="operator stepped away")), state)

    assert [e.type.value for e in events] == ["CUSTOM", "RUN_FINISHED"]
    custom, finished = events
    assert custom.name == "agentdeck.paused"
    assert custom.value == {"reason": "operator stepped away"}
    assert finished.outcome.type == "success"


def test_text_delta_then_run_paused_ends_the_text_message_first():
    state = _AdapterState(thread_id="t", run_id="r")
    _to_agui_event(_event(TextDelta(message_id="m1", text="hi")), state)

    closed = [e.type.value for e in _to_agui_event(_event(RunPaused(reason=None)), state)]

    assert closed == ["TEXT_MESSAGE_END", "CUSTOM", "RUN_FINISHED"]


def test_backend_tool_calls_and_results_project_correctly(no_project):
    model = ScriptedModel(tool_name="_lookup", tool_arguments='{"city": "NYC"}', deltas=("done",))
    with patch_model(model), _client(_deck(), AGUI.http("/agui", target="Greeter")) as client:
        events = _events_from(client.post("/agui", json=_body()))

    tool_events = [e for e in events if e["type"].startswith("TOOL_CALL")]
    assert [e["type"] for e in tool_events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    assert json.loads(tool_events[1]["delta"]) == {"city": "NYC"}
    assert tool_events[3]["content"] == "weather in NYC: sunny"


def test_ctx_ask_produces_an_agui_interrupt_carrying_question_and_options(no_project):
    with _client(_deck(), AGUI.http("/agui", target="Survey")) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        events = _events_from(client.post("/agui", json=_body(messages=content)))

    interrupt = _outcome(events)["interrupts"][0]
    assert interrupt["message"] == "pick a color for kites?"
    assert interrupt["responseSchema"] == {"enum": ["red", "blue"]}


async def test_resume_continues_the_same_agentdeck_run(no_project):
    deck = _deck()
    with _client(deck, AGUI.http("/agui", target="Survey")) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        started = _events_from(client.post("/agui", json=_body(messages=content)))
        interrupt = _outcome(started)["interrupts"][0]
        before = await deck.runs.list()

        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": "red"}]
        resumed = _events_from(client.post("/agui", json=_body(runId="r2", resume=resume)))
        after = await deck.runs.list()

    assert _outcome(resumed) == {"type": "success"}
    assert [r.id for r in before] == [r.id for r in after]


async def test_a_new_agui_run_id_does_not_replace_run_id(no_project):
    deck = _deck()
    with _client(deck, AGUI.http("/agui", target="Survey")) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        started = _events_from(client.post("/agui", json=_body(runId="r1", messages=content)))
        interrupt = _outcome(started)["interrupts"][0]
        [run] = await deck.runs.list()

        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": "red"}]
        client.post("/agui", json=_body(runId="r2", resume=resume))
        [run_after] = await deck.runs.list()

    assert run.id == run_after.id
    assert run.id not in ("r1", "r2")


async def test_an_invalid_answer_is_a_protocol_safe_error_and_the_run_stays_waiting(no_project):
    deck = _deck()
    with _client(deck, AGUI.http("/agui", target="Survey")) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        started = _events_from(client.post("/agui", json=_body(messages=content)))
        interrupt = _outcome(started)["interrupts"][0]

        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": "chartreuse"}]
        bad = client.post("/agui", json=_body(runId="r2", resume=resume))
        events = _events_from(bad)
        waiting = await deck.runs.list(status=RunStatus.WAITING_ANSWER)

    assert bad.status_code == 200
    assert events[-1]["type"] == "RUN_ERROR"
    assert len(waiting) == 1


def test_a_namespace_isolates_runs_between_two_bindings(no_project):
    bindings = (
        AGUI.http("/a", target="Survey", namespace="acme", name="agui-a"),
        AGUI.http("/b", target="Survey", namespace="elsewhere", name="agui-b"),
    )
    with _client(_deck(), *bindings) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        started = _events_from(client.post("/a", json=_body(messages=content)))
        interrupt = _outcome(started)["interrupts"][0]

        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": "red"}]
        cross_namespace = client.post("/b", json=_body(runId="r2", resume=resume))

    assert cross_namespace.status_code == 422
    assert "waiting for an answer" in cross_namespace.json()["detail"]


async def test_a_pinned_endpoint_cannot_resume_another_targets_run(no_project):
    """The same session, the same namespace, the same interrupt id  -  only the target differs.
    Neither a pinned endpoint nor an explicit ``forwardedProps.agentdeck.target`` may resume a
    run that belongs to a different target than the one this request addresses."""
    deck = _deck()
    bindings = (
        AGUI.http("/survey", target="Survey", name="agui-survey"),
        AGUI.http("/support", target="Greeter", name="agui-support"),
        AGUI.http("/agui", name="agui-catalog"),
    )
    with _client(deck, *bindings) as client:
        content = [{"id": "m1", "role": "user", "content": "kites"}]
        started = _events_from(client.post("/survey", json=_body(messages=content)))
        interrupt = _outcome(started)["interrupts"][0]

        resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": "red"}]
        cross_target_pinned = client.post("/support", json=_body(runId="r2", resume=resume))
        cross_target_unpinned = client.post("/agui", json=_targeted("Greeter", runId="r3", resume=resume))

        waiting = await deck.runs.list(status=RunStatus.WAITING_ANSWER)

    assert cross_target_pinned.status_code == 422
    assert "Survey" in cross_target_pinned.json()["detail"]
    assert cross_target_unpinned.status_code == 422
    assert "Survey" in cross_target_unpinned.json()["detail"]
    assert len(waiting) == 1


async def test_a_client_disconnect_cancels_the_run(no_project, monkeypatch):
    """``Run.cancel()`` is cooperative (``docs/patterns``), so whether a held engine ever
    reaches a safe point is that method's own contract, not this binding's; what ruling 46
    actually commits the binding to is calling it, which a spy proves directly.
    """
    calls: list[str | None] = []
    original_cancel = Run.cancel

    async def _spy_cancel(self: Run, reason: str | None = None) -> None:
        calls.append(reason)
        await original_cancel(self, reason)

    monkeypatch.setattr(Run, "cancel", _spy_cancel)

    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one", "two"), hold=hold)
    deck = _deck()
    binding = _AGUIBinding(target="Greeter")
    binding.build(DeckGateway(deck))

    with patch_model(model):
        async with deck:
            response = await binding._handle(_FakeRequest(_body()))
            consumer = asyncio.create_task(_drain(response))
            await model.holding.wait()
            await asyncio.sleep(0)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer

    assert calls == ["client disconnected"]


async def test_a_disconnect_after_the_run_is_already_past_cancelling_does_not_raise(no_project, monkeypatch):
    """``Run.cancel()`` on an already-terminal run can itself raise ``RunStateError``; the
    disconnect handler swallows it (logged at debug) so only ``CancelledError`` ever
    propagates out of the cancelled consumer task."""

    async def _already_done(self: Run, reason: str | None = None) -> None:
        raise RunStateError("run already completed")

    monkeypatch.setattr(Run, "cancel", _already_done)

    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one", "two"), hold=hold)
    deck = _deck()
    binding = _AGUIBinding(target="Greeter")
    binding.build(DeckGateway(deck))

    with patch_model(model):
        async with deck:
            response = await binding._handle(_FakeRequest(_body()))
            consumer = asyncio.create_task(_drain(response))
            await model.holding.wait()
            await asyncio.sleep(0)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer


async def test_a_gateway_error_after_the_run_starts_maps_to_run_error(no_project):
    """`SessionBusyError` (Errors table's ``after the run starts`` row) can only fire once a
    first run already holds the session, which needs it still in flight  -  `TestClient.post`
    drains a request whole, so this drives `_handle` directly, as test 16 does."""
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one", "two"), hold=hold)
    deck = _deck()
    binding = _AGUIBinding(target="Greeter")
    binding.build(DeckGateway(deck))

    with patch_model(model):
        async with deck:
            first = await binding._handle(_FakeRequest(_body(threadId="busy")))
            first_task = asyncio.create_task(_drain(first))
            await model.holding.wait()

            second = await binding._handle(_FakeRequest(_body(threadId="busy", runId="r2")))
            second_events = await _drain(second)

            hold.set()
            await first_task

    assert second_events[-1] == {"type": "RUN_ERROR", "message": second_events[-1]["message"], "code": "busy"}
    assert "in flight" in second_events[-1]["message"]


def test_internal_exception_text_never_reaches_the_wire(no_project, monkeypatch):
    import agentdeck.adapters.bindings.agui.binding as binding_module

    monkeypatch.setattr(
        binding_module, "_to_agui_event", lambda event, state: (_ for _ in ()).throw(RuntimeError("secret detail"))
    )
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck(), AGUI.http("/agui", target="Greeter")) as client:
        response = client.post("/agui", json=_body())

    assert response.status_code == 200
    assert "secret detail" not in response.text
    events = _events_from(response)
    assert events[-1] == {"type": "RUN_ERROR", "message": "internal error", "code": "internal"}


def test_an_unknown_canonical_event_kind_does_not_break_the_projection():
    event = _event(UnknownEvent(kind="some.new.kind", raw_payload={}))

    assert _to_agui_event(event, _AdapterState(thread_id="t", run_id="r")) == []


def test_every_emitted_event_validates_against_the_official_agui_models(no_project):
    model = ScriptedModel(tool_name="_lookup", tool_arguments='{"city": "NYC"}', deltas=("done",))
    with patch_model(model), _client(_deck(), AGUI.http("/agui", target="Greeter")) as client:
        events = _events_from(client.post("/agui", json=_body()))

    assert len(events) > 5
    for raw in events:
        _WIRE_EVENT.validate_python(raw)


def test_multimodal_input_maps_agui_content_to_agentdeck_blocks():
    run_input = RunAgentInput.model_validate(
        _body(
            messages=[
                {
                    "id": "m1",
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look at this"},
                        {"type": "image", "source": {"type": "data", "value": "aGVsbG8=", "mimeType": "image/png"}},
                    ],
                }
            ]
        )
    )

    blocks = _to_agentdeck_input(run_input)

    assert blocks == [TextBlock(text="look at this"), ImageBlock(media_type="image/png", data_b64="aGVsbG8=")]


def _content_input(*parts: dict) -> RunAgentInput:
    return RunAgentInput.model_validate(_body(messages=[{"id": "m1", "role": "user", "content": list(parts)}]))


def test_url_video_maps_to_a_resource_block():
    run_input = _content_input(
        {"type": "video", "source": {"type": "url", "value": "https://x/clip.mp4", "mimeType": "video/mp4"}}
    )

    assert _to_agentdeck_input(run_input) == [ResourceBlock(uri="https://x/clip.mp4", media_type="video/mp4")]


def test_inline_video_is_refused_with_a_named_reason():
    run_input = _content_input(
        {"type": "video", "source": {"type": "data", "value": "aGVsbG8=", "mimeType": "video/mp4"}}
    )

    with pytest.raises(InputError, match="inline video"):
        _to_agentdeck_input(run_input)


def test_inline_document_is_refused_with_a_named_reason():
    run_input = _content_input(
        {"type": "document", "source": {"type": "data", "value": "aGVsbG8=", "mimeType": "application/pdf"}}
    )

    with pytest.raises(InputError, match="inline document"):
        _to_agentdeck_input(run_input)


def test_binary_input_content_is_refused_with_a_named_reason():
    run_input = _content_input({"type": "binary", "mimeType": "application/octet-stream", "data": "aGVsbG8="})

    with pytest.raises(InputError, match="BinaryInputContent"):
        _to_agentdeck_input(run_input)
