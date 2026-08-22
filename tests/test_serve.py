"""The HTTP surface must answer coherently before the lifespan has started the App,
and expose the run control endpoints once it has.
"""

import subprocess
import sys
import textwrap
from functools import partial

import pytest

pytest.importorskip("fastapi")


def test_endpoints_503_before_startup():
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    client = TestClient(create_app())  # no `with`: lifespan never runs, so state.deck stays None

    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"status": "starting"}

    chat = client.post("/agents/Greeter/chat", json={"session_id": "s1", "message": "hi"})
    assert chat.status_code == 503
    assert client.post("/runs/r-1/pause").status_code == 503
    assert client.post("/runs/r-1/cancel").status_code == 503
    assert client.post("/runs/r-1/resume").status_code == 503


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    (tmp_path / ".agentdeck").mkdir()
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]

    with TestClient(create_app()) as c:
        yield c


def test_run_control_endpoints_record_a_request_and_answer_at_once(client):
    """Pause and cancel answer before the run has done anything about them  -  that is the whole
    point of the request/observation split  -  so the body says ``recorded``, never "stopped".

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


APPROVAL_WORKFLOW_PY = """
from agentdeck import WorkflowCtx, workflow


@workflow(name="Approval")
async def approval(ctx: WorkflowCtx, request: str) -> str:
    decision = await ctx.ask(request, options=["yes", "no"])
    return f"{decision}:{request}"
"""


@pytest.fixture
def approval_client(tmp_path, monkeypatch):
    """A served deck holding one native ``@workflow`` that parks on ``ctx.ask``."""
    from fastapi.testclient import TestClient

    from agentdeck.serve import create_app

    bundle = tmp_path / ".agentdeck" / "workflows" / "approval"
    bundle.mkdir(parents=True)
    (bundle / "workflow.py").write_text(textwrap.dedent(APPROVAL_WORKFLOW_PY))
    monkeypatch.chdir(tmp_path)
    for mod in [m for m in sys.modules if m.startswith("agentdeck_project")]:
        del sys.modules[mod]

    with TestClient(create_app()) as c:
        yield c


def test_resuming_a_run_that_is_waiting_for_an_answer_is_a_conflict_that_names_the_verb(approval_client):
    """The run *does* exist and *is* stopped, so neither 404 nor the generic "not paused" 409
    fits: it is waiting for a value, and the caller reached for the wrong one of the two verbs.
    A state refusal is not a server fault either  -  without its own handler ``RunStateError``
    would fall through to the catch-all and answer 500 with the message swallowed, which is
    exactly the information this endpoint exists to hand back.

    Parked through the served ``Deck`` itself, on the server's own loop: ``Deck.asgi()`` serves
    the caller's object, so application code holding it is how a run reaches ``WAITING_ANSWER``
    now that no route starts one.
    """
    deck = approval_client.app.state.deck

    async def _park() -> str:
        paused = await deck.run("Approval", "tue 9am", session_id="t-409")
        assert paused["type"] == "interrupt"
        return paused["id"]

    run_id = approval_client.portal.call(partial(_park))

    response = approval_client.post(f"/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert "run.answer(...)" in response.json()["detail"]

    async def _answer_and_await() -> str:
        run = await deck.runs.get(run_id)
        await run.answer("yes")
        return await run

    # Refused, not consumed: the approval is still answerable afterwards.
    assert approval_client.portal.call(partial(_answer_and_await)) == "yes:tue 9am"


def test_a_control_reason_that_is_not_a_string_is_refused_at_the_boundary(client):
    """The reason is recorded in the log and read by whoever asks why a run stopped, so it is
    validated where it arrives  -  422 from the edge, not a 500 out of a payload class later."""
    assert client.post("/runs/r-http/pause", json={"reason": 7}).status_code == 422


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
