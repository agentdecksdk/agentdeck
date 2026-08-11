"""Slice 2 of the reference application: the HTTP route the docs panel talks to.

The claim under test is ruling 3 of `docs/delivery/plan-219-delivery.md` — that a real embedded
application can serve a context-requiring agent over HTTP by writing its own route over
`deck.stream()`, which `Deck.asgi()` structurally cannot do. Proving it needs a model that
actually calls a tool, so this module scripts one: a Chat-Completions endpoint that answers the
first request with a `read_doc` call and everything after it with text.

That is the whole reason for the forty lines of fake server below. A model that only ever
returns text would exercise the route and prove nothing about the context, because a tool is the
only thing that reads one.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ask-agentdeck"
if str(EXAMPLE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE))

from ask_agentdeck.server import Question, build_app, page_context_input  # noqa: E402 — needs the path above

_TOOL_CALL = {
    "index": 0,
    "id": "call-1",
    "type": "function",
    "function": {"name": "read_doc", "arguments": '{"slug": "concepts/agents"}'},
}


class _ScriptedToolCallingModel(BaseHTTPRequestHandler):
    """First request: call `read_doc`. After that: plain text. Streamed, because `deck.stream`
    always uses the SDK's streaming runner and a flat completion parses as zero chunks there.
    """

    turns = 0

    def do_POST(self) -> None:
        json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).turns += 1
        first = type(self).turns == 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = (
            [
                {"delta": {"role": "assistant", "tool_calls": [_TOOL_CALL]}, "finish_reason": None},
                {"delta": {}, "finish_reason": "tool_calls"},
            ]
            if first
            else [
                {"delta": {"role": "assistant", "content": "See concepts/agents."}, "finish_reason": None},
                {"delta": {}, "finish_reason": "stop"},
            ]
        )
        for chunk in chunks:
            payload = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fake",
                "choices": [{"index": 0, **chunk}],
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def scripted_model() -> Iterator[str]:
    _ScriptedToolCallingModel.turns = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedToolCallingModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join()


@pytest.fixture
def client(scripted_model: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name, value in {
        "OPENAI_BASE_URL": scripted_model,
        "OPENAI_API_KEY": "ask-agentdeck-test",
        "OPENAI_MODEL": "fake",
        "OPENAI_USE_RESPONSES": "false",
        "AGENTDECK_EVENTS": "memory://",
        "AGENTDECK_CHECKPOINT": "memory://",
        "AGENTDECK_SESSION": "",
    }.items():
        monkeypatch.setenv(name, value)
    from agentdeck.runtime.settings import get_settings

    get_settings.cache_clear()
    with TestClient(build_app()) as opened:
        yield opened
    get_settings.cache_clear()


def _events(response) -> list[dict]:  # noqa: ANN001 — httpx.Response, not worth importing for one hint
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]


def test_the_preamble_is_absent_when_there_is_nothing_to_say() -> None:
    """A `curl` that knows about no page must not get an empty `<context>` block explaining that
    it knows about no page — that is noise in the prompt and the model will try to use it."""
    assert page_context_input(Question(question="what is a Deck?")) == "what is a Deck?"


def test_the_preamble_carries_the_page_and_the_selection() -> None:
    built = page_context_input(Question(question="explain this", page="reference/deck", selection="deck.asgi()"))
    assert "reference/deck" in built
    assert "deck.asgi()" in built
    assert built.endswith("explain this"), "the question goes last, so it is what the model answers"


def test_health_reports_the_loaded_corpus(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["pages"] > 0


def test_the_wire_is_the_canonical_event_log(client: TestClient) -> None:
    """No translation layer: each frame is one `Event`, so a browser switching on `event.kind`
    reads exactly what a later process reading the run back would read.
    """
    response = client.post("/ask", json={"question": "how do I create an agent?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    kinds = [event["kind"] for event in _events(response)]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"


def test_the_page_the_reader_is_on_reaches_the_run(client: TestClient) -> None:
    """The page context contract, end to end over HTTP — asserted on `run.started`'s own input,
    which is what the event log will show anyone reading the run back.
    """
    response = client.post("/ask", json={"question": "explain this", "page": "reference/deck"})
    started = next(event for event in _events(response) if event["kind"] == "run.started")
    text = " ".join(block.get("text", "") for block in started["payload"]["input"])
    assert "reference/deck" in text


def test_the_context_reaches_a_tool_on_a_served_run(client: TestClient) -> None:
    """**Ruling 3, demonstrated.** The scripted model calls `read_doc`, whose only way to answer
    is `docs.data.pages` — so a served run that reached the tool with no context would come back
    with an error instead of a page. Through `Deck.asgi()` this is impossible by construction;
    through the application's own route it simply works.
    """
    response = client.post("/ask", json={"question": "how do I create an agent?"})
    completed = [event for event in _events(response) if event["kind"] == "tool.call.completed"]
    assert completed, "the scripted model called read_doc, but no tool completion reached the log"
    assert "title: Agents" in completed[0]["payload"]["result_preview"], completed[0]


def test_a_session_id_is_carried_onto_the_run(client: TestClient) -> None:
    """The panel keeps one conversation per reader, so a follow-up question has to land on the
    same session the first one did."""
    response = client.post("/ask", json={"question": "and then?", "session_id": "reader-1"})
    assert all(event["session_id"] == "reader-1" for event in _events(response))
