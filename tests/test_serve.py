"""The HTTP surface must answer coherently before the lifespan has started the App,
and expose the interrupt inbox / resume pair once it has.
"""

import sys
import textwrap

import pytest

pytest.importorskip("fastapi")

APPROVAL_WORKFLOW_PY = """
from pydantic import BaseModel
from agentdeck.workflows import END, BaseWorkflow, StateGraph, interrupt

class State(BaseModel):
    request: str = ""
    decision: str = ""
    outcome: str = ""

class ApprovalFlow(BaseWorkflow):
    state = State
    durable = True

    @classmethod
    def build_graph(cls):
        g = StateGraph(cls.state)
        g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
        g.add_node("done", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
        g.set_entry_point("ask")
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


def test_workflow_interrupt_inbox_and_resume(tmp_path, monkeypatch):
    """Pause over HTTP, read the inbox, resume with a decision — the Middle approval loop."""
    from fastapi.testclient import TestClient

    from agentdeck.runtime.settings import reset_settings_cache
    from agentdeck.serve import create_app

    root = tmp_path / ".agentdeck" / "approval_flow"
    root.mkdir(parents=True)
    (root / "workflow.py").write_text(textwrap.dedent(APPROVAL_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTDECK_CHECKPOINT_BACKEND", "memory")
    reset_settings_cache()
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]

    with TestClient(create_app()) as client:
        paused = client.post("/workflows/ApprovalFlow?thread_id=t-http", json={"request": "tue 9am"})
        pending = client.get("/workflows/ApprovalFlow/pending")
        resumed = client.post("/workflows/ApprovalFlow/t-http/resume", json={"value": "yes"})
        missing_value = client.post("/workflows/ApprovalFlow/t-http/resume", json={})
        empty = client.get("/workflows/ApprovalFlow/pending")

    expected = {"type": "interrupt", "payload": {"question": "tue 9am"}, "thread_id": "t-http"}
    assert paused.json() == expected
    assert pending.json() == [expected]
    assert resumed.json()["outcome"] == "booked"
    assert missing_value.status_code == 422
    assert empty.json() == []
    reset_settings_cache()
