"""`Native.http()` end to end through `Exposure.asgi()` (`docs/design/protocols/native-wire.md`):
every route and status code over the wire, SSE reconnect from `Last-Event-ID`, and an interrupt
answered and re-tailed.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket  # noqa: TC003 (get_type_hints() resolves `_unschematizable`'s annotation at runtime)
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from agentdeck import WorkflowCtx, workflow
from agentdeck.adapters.bindings.native.binding import _NativeBinding, _on_unsupported
from agentdeck.authoring import Agent
from agentdeck.bindings.native import Native
from agentdeck.deck import Deck
from agentdeck.errors import UnsupportedControlError
from agentdeck.testing import ScriptedModel, patch_model

NATIVE_WIRE_DOC = Path(__file__).resolve().parents[2] / "docs" / "design" / "protocols" / "native-wire.md"
_ROUTE_ROW = re.compile(r"^\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`([^`]+)`")


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


async def _unschematizable(ctx: WorkflowCtx, conn: socket.socket) -> str:
    return "unreachable"  # never called: the point is the schema pydantic can't build for `conn`


def _deck() -> Deck:
    return Deck(
        agents=[Agent(name="Greeter", instructions="Greet the user.")],
        workflows=[workflow(_survey, name="Survey")],
    )


def _client(deck: Deck) -> TestClient:
    # raise_server_exceptions=False: Starlette's own 500 handler re-raises after responding, so
    # the server still logs the bug. A client sees the response, which is what these assert.
    return TestClient(deck.expose(Native.http()).asgi(), raise_server_exceptions=False)


def _events_from(response) -> list[dict]:
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


def _ids_from(response) -> list[int]:
    return [int(line.removeprefix("id: ")) for line in response.text.splitlines() if line.startswith("id: ")]



def test_targets_lists_every_agent_and_workflow(no_project):
    with _client(_deck()) as client:
        response = client.get("/targets")

    assert response.status_code == 200
    assert {t["name"] for t in response.json()} == {"Greeter", "Survey"}


def test_targets_maps_a_schema_generation_failure_to_internal_500_without_echoing_it(no_project):
    """`_workflow_schema` calls pydantic's `create_model(...).model_json_schema()`; a parameter
    type pydantic cannot schematize must not reach the client as a bare, non-JSON 500."""
    deck = Deck(workflows=[workflow(_unschematizable, name="Bad")])
    with _client(deck) as client:
        response = client.get("/targets")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}


def test_start_run_returns_identity_then_get_run_reads_it_back(no_project):
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        started = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "s1"})
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        assert started.json()["session_id"] == "s1"

        client.get(f"/runs/{run_id}/events")  # drain the segment: forces the run to finish
        fetched = client.get(f"/runs/{run_id}")

    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id
    assert fetched.json()["status"] == "completed"


def test_list_runs_filters_by_status(no_project):
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        for session in ("s1", "s2"):
            started = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": session})
            client.get(f"/runs/{started.json()['run_id']}/events")  # force completion

        completed = client.get("/runs", params={"status": "completed"})
        waiting = client.get("/runs", params={"status": "waiting_answer"})

    assert len(completed.json()) == 2
    assert waiting.json() == []


def test_events_streams_raw_event_json_with_seq_as_id(no_project):
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        started = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "s1"})
        response = client.get(f"/runs/{started.json()['run_id']}/events")

    events, ids = _events_from(response), _ids_from(response)
    assert [e["seq"] for e in events] == ids
    assert events[0]["kind"] == "run.started"
    assert events[-1]["kind"] == "run.completed"


def test_events_reconnect_with_last_event_id_resumes_after_that_seq(no_project):
    model = ScriptedModel(deltas=("hi", "there"))
    with patch_model(model), _client(_deck()) as client:
        started = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "s1"})
        run_id = started.json()["run_id"]

        full = _events_from(client.get(f"/runs/{run_id}/events"))
        midpoint = full[1]["seq"]

        resumed = client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(midpoint)})

    resumed_events = _events_from(resumed)
    assert [e["seq"] for e in resumed_events] == [e["seq"] for e in full if e["seq"] > midpoint]


def test_pending_and_answer_over_the_wire_then_re_tail(no_project):
    with _client(_deck()) as client:
        started = client.post("/runs", json={"target": "Survey", "input": "kites", "session_id": "s1"})
        run_id = started.json()["run_id"]

        first_tail = _events_from(client.get(f"/runs/{run_id}/events"))
        assert first_tail[-1]["kind"] == "run.interrupted"
        interrupt_seq = first_tail[-1]["seq"]

        pending = client.get(f"/runs/{run_id}/pending")
        assert pending.json()["payload"]["question"] == "pick a color for kites?"

        # waiting for a value refuses resume: the RunStateError/CONFLICT path, with the
        # operation that would work named in the message (`run.answer`).
        resumed_too_early = client.post(f"/runs/{run_id}/resume")
        assert resumed_too_early.status_code == 409
        assert "answer" in resumed_too_early.json()["detail"]

        answered = client.post(f"/runs/{run_id}/answer", json={"value": "red"})
        assert answered.status_code == 200

        resumed = _events_from(client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": str(interrupt_seq)}))

    # re-tailing from interrupt_seq + 1 means the resumed segment never re-walks the interrupt.
    assert all(e["seq"] > interrupt_seq for e in resumed)
    assert resumed[-1]["kind"] == "run.completed"


def test_cancel_and_resume_are_quiet_no_ops_on_a_terminal_run(no_project):
    """``Run.cancel``/``Run.resume`` both return quietly once a run has ended. Nothing to stop
    or lift, so the route answers 200 rather than an error (``core/status.py``'s own table)."""
    model = ScriptedModel(deltas=("hi",))
    with patch_model(model), _client(_deck()) as client:
        started = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "s1"})
        run_id = started.json()["run_id"]
        client.get(f"/runs/{run_id}/events")  # drain: the run is terminal from here on

        cancelled = client.post(f"/runs/{run_id}/cancel")
        resumed = client.post(f"/runs/{run_id}/resume")

    assert cancelled.status_code == 200
    assert resumed.status_code == 200



def test_unknown_run_id_maps_to_404(no_project):
    with _client(_deck()) as client:
        response = client.get("/runs/no-such-run")

    assert response.status_code == 404


def test_events_for_an_unknown_run_id_is_404_json_not_a_dying_stream(no_project):
    """The property `_events`'s own docstring claims: pulling the first event before the
    response is built means a refusal is a status code, never a `text/event-stream` body that
    opens and immediately stops. Proven here, not merely asserted in the docstring."""
    with _client(_deck()) as client:
        response = client.get("/runs/no-such-run/events")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_events_for_a_run_in_another_namespace_is_404(no_project):
    """``namespace`` is fixed per binding (ruling 5): a run started under one binding's
    namespace is unreachable, not merely re-scoped, through a second binding on another one."""
    model = ScriptedModel(deltas=("hi",))
    deck = _deck()
    default_binding = Native.http(path="/default")
    other_binding = Native.http(path="/other", namespace="elsewhere", name="native-elsewhere")
    with patch_model(model), TestClient(deck.expose(default_binding, other_binding).asgi()) as client:
        started = client.post("/default/runs", json={"target": "Greeter", "input": "hi", "session_id": "s1"})
        run_id = started.json()["run_id"]

        response = client.get(f"/other/runs/{run_id}/events")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_second_run_on_a_busy_session_maps_to_409(no_project):
    model = ScriptedModel(deltas=("hi",), hold=asyncio.Event())  # held forever: the session stays claimed
    with patch_model(model), _client(_deck()) as client:
        first = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "busy"})
        assert first.status_code == 201
        second = client.post("/runs", json={"target": "Greeter", "input": "hi", "session_id": "busy"})

    assert second.status_code == 409


def test_missing_target_field_maps_to_422(no_project):
    with _client(_deck()) as client:
        response = client.post("/runs", json={"input": "hi"})

    assert response.status_code == 422
    assert "target" in response.json()["detail"]


def test_answering_outside_the_offered_options_maps_to_422(no_project):
    """`run.answer()` refuses a value the ask did not offer with `InputError`, which is caller
    input, not a bug: 422, not 500."""
    deck = _deck()
    with _client(deck) as client:
        started = client.post("/runs", json={"target": "Survey", "input": {"topic": "kites"}, "session_id": "s1"})
        run_id = started.json()["run_id"]
        client.get(f"/runs/{run_id}/events")

        response = client.post(f"/runs/{run_id}/answer", json={"value": "chartreuse"})

    assert response.status_code == 422
    assert "waiting for one of" in response.json()["detail"]


def test_a_non_object_json_body_is_422_not_500(no_project):
    deck = _deck()
    with _client(deck) as client:
        for body in ("[]", "7", '"hello"', "null"):
            response = client.post("/runs", content=body, headers={"content-type": "application/json"})
            assert response.status_code == 422, body
            assert "object" in response.json()["detail"]


def test_a_negative_limit_is_422_not_a_store_error(no_project):
    deck = _deck()
    with _client(deck) as client:
        response = client.get("/runs", params={"limit": "-1"})

    assert response.status_code == 422
    assert "negative" in response.json()["detail"]


def test_unknown_status_query_maps_to_422_naming_accepted_values(no_project):
    with _client(_deck()) as client:
        response = client.get("/runs", params={"status": "bogus"})

    assert response.status_code == 422
    assert "completed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unsupported_control_translates_to_501() -> None:
    """Translated directly rather than over the wire: every deck a supported configuration can
    build has a control port, and reaching this state needed `deck._runtime._control = None`.
    """
    response = await _on_unsupported(None, UnsupportedControlError("run r1 cannot pause: set AGENTDECK_CONTROL"))

    assert response.status_code == 501
    assert "AGENTDECK_CONTROL" in json.loads(bytes(response.body))["detail"]


def _doc_routes() -> set[tuple[str, str]]:
    section = NATIVE_WIRE_DOC.read_text().split("## Routes", 1)[1].split("## Errors", 1)[0]
    return {(m.group(1), m.group(2)) for line in section.splitlines() if (m := _ROUTE_ROW.match(line.strip()))}


def _app_routes() -> set[tuple[str, str]]:
    # build() is pure and never reads the gateway (only stores it), so a placeholder proves the
    # route table without needing a real Deck.
    endpoint = _NativeBinding().build(object())  # ty: ignore[invalid-argument-type]
    return {
        (method, route.path)
        for route in endpoint.app.routes
        for method in (route.methods or set())
        if method not in ("HEAD", "OPTIONS")
    }


def test_native_wire_doc_route_table_matches_the_app():
    """The route table only: request shapes and status codes are proven by the tests above, not
    by parsing the doc."""
    assert _doc_routes() == _app_routes()
