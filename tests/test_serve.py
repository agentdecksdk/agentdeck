"""The HTTP surface must answer coherently before the lifespan has started the App,
and expose the interrupt inbox / resume pair once it has.
"""

import json
import subprocess
import sys
import textwrap
from functools import partial

import pytest

from agentdeck.core.status import RunStatus

pytest.importorskip("fastapi")

APPROVAL_WORKFLOW_PY = """
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel
from agentdeck.authoring import Workflow

class State(BaseModel):
    request: str = ""
    prepared: int = 0
    decision: str = ""
    outcome: str = ""

def _build_graph():
    g = StateGraph(State)
    g.add_node("prepare", lambda s: {"prepared": s.prepared + 1})
    g.add_node("ask", lambda s: {"decision": interrupt({"question": s.request})})
    g.add_node("done", lambda s: {"outcome": "booked" if s.decision == "yes" else "dropped"})
    g.set_entry_point("prepare")
    g.add_edge("prepare", "ask")
    g.add_edge("ask", "done")
    g.add_edge("done", END)
    return g

approval_flow = Workflow(name="ApprovalFlow", state=State, durable=True, graph=_build_graph)
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


@pytest.fixture(params=["memory", "sqlite"])
def client(request, tmp_path, monkeypatch):
    """Every case here on both checkpoint backends: a durable workflow resumes through the
    rerouted endpoints on whichever one the project configured, and pinning only ``memory``
    would leave a bridge that quietly compiled a saver of its own indistinguishable."""
    from fastapi.testclient import TestClient

    from agentdeck.runtime.settings import reset_settings_cache
    from agentdeck.serve import create_app

    if request.param == "sqlite":
        monkeypatch.setenv("AGENTDECK_CHECKPOINT", f"sqlite://{tmp_path / 'checkpoints.sqlite3'}")
    else:
        monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    root = tmp_path / ".agentdeck" / "workflows" / "approval_flow"
    root.mkdir(parents=True)
    (root / "workflow.py").write_text(textwrap.dedent(APPROVAL_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    reset_settings_cache()
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]

    with TestClient(create_app()) as c:
        yield c
    reset_settings_cache()


def _in_the_servers_loop(client, method, *args, **kwargs):
    """One Python-API call on the loop the server itself runs on.

    An async checkpointer's connection binds to the loop that opened it, so a fresh
    ``asyncio.run`` would fail on that rather than exercise anything — and a second process is
    not what this models anyway: it is one deployment answering an approval through both of its
    front doors.
    """
    return client.portal.call(partial(method, *args, **kwargs))


async def _run_waiting_on(deck, thread_id):
    """The ``Run`` parked on ``thread_id`` — the public surface's own way to the id these tests
    used to get off ``deck.runs.pending()``'s ``PendingRun`` rows."""
    for run in await deck.runs.list(status=RunStatus.WAITING_ANSWER):
        pending = await run.pending()
        if pending is not None and pending["thread_id"] == thread_id:
            return run
    raise AssertionError(f"no run waiting on thread {thread_id!r}")


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


def test_resuming_a_run_that_is_waiting_for_an_answer_is_a_conflict_that_names_the_verb(client):
    """The run *does* exist and *is* stopped, so neither 404 nor the generic "not paused" 409
    fits: it is waiting for a value, and the caller reached for the wrong one of the two verbs.
    A state refusal is not a server fault either — without its own handler ``RunStateError``
    would fall through to the catch-all and answer 500 with the message swallowed, which is
    exactly the information this endpoint exists to hand back.
    """
    deck = client.app.state.deck
    client.post("/workflows/ApprovalFlow?thread_id=t-wrong-verb", json={"request": "tue 9am"})
    mine = _in_the_servers_loop(client, _run_waiting_on, deck, "t-wrong-verb")

    response = client.post(f"/runs/{mine.id}/resume")

    assert response.status_code == 409, response.text
    assert "run.answer(...)" in response.json()["detail"]

    async def _answer_and_await():
        await mine.answer("yes")
        return await mine

    # Refused, not consumed: the approval is still answerable, which also keeps the process-wide
    # memory saver clean for the tests after this one.
    answered = _in_the_servers_loop(client, _answer_and_await)
    assert answered["outcome"] == "booked"


def test_a_control_reason_that_is_not_a_string_is_refused_at_the_boundary(client):
    """The reason is recorded in the log and read by whoever asks why a run stopped, so it is
    validated where it arrives — 422 from the edge, not a 500 out of a payload class later."""
    assert client.post("/runs/r-http/pause", json={"reason": 7}).status_code == 422


@pytest.mark.parametrize(
    ("query", "thread"), [("", "t-busy-body"), ("&stream=true", "t-busy-sse")], ids=["body", "streamed"]
)
def test_a_second_run_on_a_thread_parked_on_an_approval_is_a_409(client, query, thread):
    """The refusal has to be an *answer*, streamed or not. A streamed handler that hands its
    generator straight to ``StreamingResponse`` has committed ``200`` and ``text/event-stream``
    before the session claim is even attempted, so the refusal can only arrive in-band as
    ``event: error`` — a body that stops, indistinguishable from a run that produced nothing.

    This is the case an approval UI actually hits: the thread is *idle*, waiting on a human, and
    goes on holding its session until somebody answers it.
    """
    parked = client.post(f"/workflows/ApprovalFlow?thread_id={thread}", json={"request": "tue 9am"})
    assert parked.json()["type"] == "interrupt"

    second = client.post(f"/workflows/ApprovalFlow?thread_id={thread}{query}", json={"request": "wed 4pm"})

    assert second.status_code == 409, second.text
    assert thread in second.json()["detail"]
    assert "event: error" not in second.text
    # The refusal left the approval exactly as it was, and answering it here also keeps the
    # process-wide memory saver clean for the tests after this one.
    answered = client.post(f"/workflows/ApprovalFlow/{thread}/resume", json={"value": "yes"})
    assert answered.json() == {"request": "tue 9am", "prepared": 1, "decision": "yes", "outcome": "booked"}


def test_resuming_a_thread_already_answered_out_of_band_is_a_404_not_a_dropped_value(client):
    """A thread answered once, through either front door, must not be resumable a second time.
    Resuming an already-answered thread replays it past ``END``, which langgraph does happily —
    returning its stale final state and dropping this caller's value on the floor. Answering
    404 is the only honest report, and it has to hold whichever door answered it first.
    """
    deck = client.app.state.deck
    client.post("/workflows/ApprovalFlow?thread_id=t-ghost", json={"request": "tue 9am"})
    mine = _in_the_servers_loop(client, _run_waiting_on, deck, "t-ghost")

    async def _answer_and_await():
        await mine.answer("yes")
        return await mine

    answered = _in_the_servers_loop(client, _answer_and_await)
    assert answered["outcome"] == "booked"

    ghost = client.post("/workflows/ApprovalFlow/t-ghost/resume", json={"value": "no"})

    assert ghost.status_code == 404, ghost.json()
    assert "t-ghost" in ghost.json()["detail"]
    # A 404 body and nothing else: the stale state must not travel back as if it were an answer.
    assert set(ghost.json()) == {"detail"}
    # The ghost is gone from the inbox too, because the refused resume closed its run in the log.
    assert _inbox(client, "t-ghost") == []


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_from_a_directory_with_no_project(tmp_path, flag):
    """Regression for #245: ``main()`` used to ignore its arguments and call ``create_app()``
    unconditionally, so ``agentdeck-serve --help`` crashed with a raw ``FileNotFoundError`` for
    the missing ``.agentdeck`` before argparse ever saw the flag. Run from an empty ``tmp_path``
    so a project mount would fail loudly if one were attempted.
    """
    result = subprocess.run(
        [sys.executable, "-m", "agentdeck.serve", flag],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: agentdeck-serve" in result.stdout
    assert ".agentdeck" in result.stdout
    assert "HOST" in result.stdout
    assert "PORT" in result.stdout


def test_an_unknown_argument_exits_2_with_usage_instead_of_being_ignored(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "agentdeck.serve", "--project-dir", "elsewhere"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "usage: agentdeck-serve" in result.stderr


def test_no_arguments_keeps_todays_host_and_port_env_defaults(monkeypatch):
    """No flags must still read ``HOST``/``PORT`` from the environment and hand them straight to
    ``uvicorn.run``, exactly like the pre-#245 unconditional call did.
    """
    from agentdeck import serve

    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setattr(serve, "create_app", lambda: "the-app")
    calls = []
    fake_uvicorn = type(
        "_FakeUvicorn", (), {"run": staticmethod(lambda app, host, port: calls.append((app, host, port)))}
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    serve.main([])

    assert calls == [("the-app", "127.0.0.1", 9001)]
