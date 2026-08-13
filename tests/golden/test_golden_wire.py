"""Byte-level baseline of the HTTP/SSE wire format, recorded against v1.2.x.

Every case is the raw response body of one request against the real ``serve.py`` app.
Re-record deliberately with ``make golden``; a diff here means the wire changed.
"""

import os
from pathlib import Path

# own path, not conftest's: `conftest` is not a unique module name across test dirs
SNAPSHOTS = Path(__file__).parent / "snapshots"

UPDATE = os.getenv("AGENTDECK_GOLDEN_UPDATE") == "1"
THREAD = "t-golden"
FANOUT_THREAD = "t-golden-fanout"
FANOUT_STREAM_THREAD = "t-golden-fanout-stream"
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
    recorded = {
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
        "12_workflow_error.http": _record(client.post("/workflows/BoomFlow", json={"text": "x"})),
        "13_workflow_error_stream.http": _record(client.post("/workflows/BoomFlow?stream=true", json={"text": "x"})),
        # A node that only has side effects reports no update at all, which v1's wire showed as
        # `"delta": null` — the commonest node shape there is, and unpinned until now.
        "14_side_effect.http": _record(client.post("/workflows/SideEffectFlow", json={"request": "x"})),
        "15_side_effect_stream.http": _record(
            client.post("/workflows/SideEffectFlow?stream=true", json={"request": "x"})
        ),
        # #122: a fan-out whose one branch interrupts while a sibling completes — the sibling's
        # `node_update` must reach the wire before the `interrupt` that replaces `done`, not be
        # dropped with the rest of what the engine drains once the pause is detected.
        "16_fanout_interrupt.http": _record(
            client.post(f"/workflows/FanoutInterruptFlow?thread_id={FANOUT_THREAD}", json={"request": "approve?"})
        ),
        "17_fanout_interrupt_stream.http": _record(
            client.post(
                f"/workflows/FanoutInterruptFlow?stream=true&thread_id={FANOUT_STREAM_THREAD}",
                json={"request": "approve?"},
            )
        ),
        # A node raising a plain ValueError, not routed through agentdeck's own error
        # taxonomy — the catch-all handler's 500, distinct from BoomFlow's AgentdeckError one.
        # No streamed twin: the streamed path already catches bare Exception in compat.py,
        # already pinned by `test_stream_endpoint_reports_mid_stream_failure`.
        "18_workflow_crash.http": _record(client.post("/workflows/CrashFlow", json={"text": "x"})),
    }
    # Not recorded: #122's Done-when only asks for the pause shape, not a second resume case
    # (that path is already pinned by 10/11). Left unanswered, though, these two threads would
    # stay parked in the process-wide `MemorySaver` singleton for the rest of the test session
    # and leak into whatever else calls `pending()` against it — so both are resumed here purely
    # to leave the shared checkpointer as clean as ApprovalFlow's own thread already is.
    for thread in (FANOUT_THREAD, FANOUT_STREAM_THREAD):
        client.post(f"/workflows/FanoutInterruptFlow/{thread}/resume", json={"value": "yes"})
    return recorded


def test_wire_matches_snapshots(make_client):
    with make_client() as client:
        recorded = capture(client)
    if UPDATE:
        for stale in {p.name for p in SNAPSHOTS.iterdir()} - set(recorded):
            (SNAPSHOTS / stale).unlink()  # a renamed case must not leave its old file behind
        for name, body in recorded.items():
            (SNAPSHOTS / name).write_bytes(body)
        return
    for name, body in recorded.items():
        assert body == (SNAPSHOTS / name).read_bytes(), f"wire changed: {name}"
    assert sorted(recorded) == sorted(p.name for p in SNAPSHOTS.iterdir())


def test_failures_never_echo_the_error_message(make_client):
    """The 500 body and the SSE error frame carry a type name only — never the message."""
    with make_client() as client:
        # importable only once App has mounted ./.agentdeck as `agentdeck_project`
        from agentdeck_project.workflows.boom_flow.workflow import SECRET as BOOM_SECRET
        from agentdeck_project.workflows.crash_flow.workflow import SECRET as CRASH_SECRET

        recorded = capture(client)
    for name in ("12_workflow_error.http", "13_workflow_error_stream.http"):
        assert BOOM_SECRET.encode() not in recorded[name]
    assert CRASH_SECRET.encode() not in recorded["18_workflow_crash.http"]


def test_capture_is_stable_across_runs(make_client):
    """Same requests, two independent app instances, same bytes — the capture is reproducible."""
    with make_client() as client:
        first = capture(client)
    with make_client() as client:
        second = capture(client)
    assert first == second
