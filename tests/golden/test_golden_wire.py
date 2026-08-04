"""Byte-level baseline of the HTTP/SSE wire format, recorded against v1.2.x.

Every case is the raw response body of one request against the real ``serve.py`` app.
Re-record deliberately with ``make golden``; a diff here means the wire changed.
"""

import os

from conftest import SNAPSHOTS

UPDATE = os.getenv("AGENTDECK_GOLDEN_UPDATE") == "1"
THREAD = "t-golden"
CHAT = {"session_id": "s-golden", "message": "any slot tuesday?"}
# date / server / content-length are transport noise, not our wire contract.
RECORDED_HEADERS = ("content-type", "cache-control", "x-accel-buffering")


def _record(response) -> bytes:
    """Status line + the headers we own + the raw body, unmodified."""
    head = [f"HTTP {response.status_code}"]
    head += [f"{h}: {response.headers[h]}" for h in RECORDED_HEADERS if h in response.headers]
    return "\n".join(head).encode() + b"\n\n" + response.content


def capture(client) -> dict[str, bytes]:
    """Every recorded exchange, in order — the interrupt cases share one thread."""
    return {
        "01_health.http": _record(client.get("/health")),
        "02_chat.http": _record(client.post("/agents/Greeter/chat", json=CHAT)),
        "03_chat_stream.http": _record(client.post("/agents/Greeter/chat?stream=true", json=CHAT)),
        "04_chat_missing_field.http": _record(client.post("/agents/Greeter/chat", json={"message": "hi"})),
        "05_agent_unknown.http": _record(client.post("/agents/Nope/chat", json=CHAT)),
        "06_workflow.http": _record(client.post("/workflows/EchoFlow", json={"text": "hello"})),
        "07_workflow_stream.http": _record(client.post("/workflows/EchoFlow?stream=true", json={"text": "hello"})),
        "08_interrupt_stream.http": _record(
            client.post(f"/workflows/ApprovalFlow?stream=true&thread_id={THREAD}", json={"request": "tue 9am"})
        ),
        "09_pending.http": _record(client.get("/workflows/ApprovalFlow/pending")),
        "10_resume.http": _record(client.post(f"/workflows/ApprovalFlow/{THREAD}/resume", json={"value": "yes"})),
        "11_pending_after_resume.http": _record(client.get("/workflows/ApprovalFlow/pending")),
    }


def test_wire_matches_snapshots(make_client):
    with make_client() as client:
        recorded = capture(client)
    if UPDATE:
        for name, body in recorded.items():
            (SNAPSHOTS / name).write_bytes(body)
        return
    for name, body in recorded.items():
        assert body == (SNAPSHOTS / name).read_bytes(), f"wire changed: {name}"
    assert sorted(recorded) == sorted(p.name for p in SNAPSHOTS.iterdir())


def test_capture_is_stable_across_runs(make_client):
    """Same requests, two independent app instances, same bytes — the capture is reproducible."""
    with make_client() as client:
        first = capture(client)
    with make_client() as client:
        second = capture(client)
    assert first == second
