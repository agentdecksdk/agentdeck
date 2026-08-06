"""The HTTP surface must answer coherently before the lifespan has started the App,
and expose the interrupt inbox / resume pair once it has.
"""

import json
import sys
import textwrap

import pytest

pytest.importorskip("fastapi")

APPROVAL_WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt

class State(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""

class ApprovalFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("prepare", lambda s: {"prepared": s.prepared + 1})
        g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
        g.add_node("done", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
        g.set_entry_point("prepare")
        g.add_edge("prepare", "ask")
        g.add_edge("ask", "done")
        g.add_edge("done", END)
        return g
"""


def test_endpoints_503_before_startup():
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    client = TestClient(create_app())  # no `with`: lifespan never runs, so state.deck stays None

    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"status": "starting"}

    chat = client.post("/agents/Greeter/chat", json={"session_id": "s1", "message": "hi"})
    assert chat.status_code == 503
    assert client.post("/workflows/HelloFlow", json={}).status_code == 503
    assert client.get("/workflows/HelloFlow/pending").status_code == 503
    assert client.post("/workflows/HelloFlow/t1/resume", json={"value": "yes"}).status_code == 503
    assert client.post("/runs/r-1/pause").status_code == 503
    assert client.post("/runs/r-1/cancel").status_code == 503
    assert client.post("/runs/r-1/resume").status_code == 503


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agentdeck.runtime.settings import reset_settings_cache
    from agentdeck.serve import create_app

    root = tmp_path / ".agentdeck" / "workflows" / "approval_flow"
    root.mkdir(parents=True)
    (root / "workflow.py").write_text(textwrap.dedent(APPROVAL_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
    reset_settings_cache()
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]

    with TestClient(create_app()) as c:
        yield c
    reset_settings_cache()


def _inbox(client, thread_id):
    """The pending list scoped to one thread — the memory saver is shared process-wide."""
    return [p for p in client.get("/workflows/ApprovalFlow/pending").json() if p["thread_id"] == thread_id]


def test_workflow_interrupt_inbox_and_resume(client):
    """Pause over HTTP, read the inbox, resume with a decision — the Middle approval loop."""
    paused = client.post("/workflows/ApprovalFlow?thread_id=t-http", json={"request": "tue 9am"})
    pending = _inbox(client, "t-http")
    resumed = client.post("/workflows/ApprovalFlow/t-http/resume", json={"value": "yes"})
    missing_value = client.post("/workflows/ApprovalFlow/t-http/resume", json={})

    expected = {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-http"}
    assert paused.json() == expected
    assert pending == [expected]
    assert resumed.json()["outcome"] == "booked"
    assert missing_value.status_code == 422
    assert _inbox(client, "t-http") == []  # answered, so it left the inbox


def test_workflow_stream_endpoint_emits_an_interrupt_event_instead_of_done(client):
    response = client.post("/workflows/ApprovalFlow?stream=true&thread_id=t-sse", json={"request": "wed 4pm"})

    frames = []
    for block in response.text.strip().split("\n\n"):
        name = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        frames.append((name, json.loads(data)))

    assert frames == [
        ("message", {"type": "node_update", "node": "prepare", "delta": {"prepared": 1}}),
        ("interrupt", {"type": "interrupt", "payload": {"question": "wed 4pm"}, "thread_id": "t-sse"}),
    ]
    assert _inbox(client, "t-sse")  # the streamed pause is a real checkpoint, waiting in the inbox
    assert client.post("/workflows/ApprovalFlow/t-sse/resume", json={"value": "no"}).json()["outcome"] == "dropped"


def test_run_control_endpoints_record_a_request_and_answer_at_once(client):
    """Pause and cancel answer before the run has done anything about them — that is the whole
    point of the request/observation split — so the body says ``recorded``, never "stopped".

    An unknown ``run_id`` is accepted for the same reason a signal against a finished run is a
    no-op: from here, a run in another process, a run that just ended and a run that never
    existed are the same thing, and refusing one would mean guessing which.
    """
    paused = client.post("/runs/r-http/pause", json={"reason": "operator stepped away"})
    cancelled = client.post("/runs/r-http/cancel")

    assert paused.status_code == 200
    assert paused.json() == {"run_id": "r-http", "verb": "pause", "recorded": True}
    assert cancelled.json() == {"run_id": "r-http", "verb": "cancel", "recorded": True}


def test_resuming_a_run_that_is_not_paused_is_a_conflict_not_a_success(client):
    """409 rather than an empty 200: "nothing to resume" is an answer a caller has to see, and a
    body that just looked like a short run would hide it."""
    response = client.post("/runs/r-not-paused/resume")

    assert response.status_code == 409
    assert "not paused" in response.json()["detail"]


def test_a_control_reason_that_is_not_a_string_is_refused_at_the_boundary(client):
    """The reason is recorded in the log and read by whoever asks why a run stopped, so it is
    validated where it arrives — 422 from the edge, not a 500 out of a payload class later."""
    assert client.post("/runs/r-http/pause", json={"reason": 7}).status_code == 422
