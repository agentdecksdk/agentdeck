"""One canonical example per event kind, shared by the round-trip and golden tests.

Every value here is a literal: these examples are serialized byte-for-byte into
``snapshots/``, so nothing may vary between runs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentdeck.core import (
    ArtifactCreated,
    Budget,
    ControlObserved,
    ControlRequested,
    Custom,
    DataBlock,
    Event,
    ImageBlock,
    InputAppended,
    MessageCompleted,
    NodeUpdated,
    ProgressReported,
    ResourceBlock,
    RunCancelled,
    RunCompleted,
    RunContextSnapshot,
    RunFailed,
    RunInterrupted,
    RunPaused,
    RunResumed,
    RunStarted,
    StatusReported,
    TextBlock,
    TextDelta,
    ThoughtDelta,
    ToolCallCompleted,
    ToolCallStarted,
    Usage,
    UsageReported,
)

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
USAGE = Usage(input_tokens=11, output_tokens=5, usd=0.0004)

PAYLOADS = (
    RunStarted(
        invocable="Greeter",
        kind_of_invocable="agent",
        parent_run_id=None,
        input=[
            TextBlock(text="any slot tuesday?"),
            ImageBlock(media_type="image/png", data_b64="iVBORw0="),
            DataBlock(data={"calendar_id": "cal_9", "attendees": ["sagi", "dana"]}),
        ],
        context=RunContextSnapshot(
            principal="user:sagi",
            trace_id="trace_1",
            budget=Budget(max_usd=1.0, max_tokens=10000),
            triggered_by="http",
        ),
    ),
    RunCompleted(
        output=[
            TextBlock(text="Tuesday at 9am works."),
            DataBlock(data={"slot": "2026-01-06T09:00:00Z", "confirmed": True}),
        ],
        usage=USAGE,
    ),
    RunFailed(error_code="tool_error", message="lookup_slot exited 1", retryable=True),
    RunPaused(reason="awaiting approval"),
    RunResumed(reason="approved", value=[DataBlock(data={"approved": True, "note": "book it"})]),
    RunCancelled(reason="operator"),
    RunInterrupted(
        interrupt_id="int_1",
        reason="approval",
        payload={"question": "book tuesday 9am?"},
        thread_id="t-1",
        expected_resume="yes|no",
    ),
    TextDelta(message_id="msg_1", text="Tuesday "),
    ThoughtDelta(message_id="msg_1", text="checking the calendar"),
    MessageCompleted(message_id="msg_1", text="Tuesday at 9am works."),
    ToolCallStarted(call_id="call_1", tool="lookup_slot", args={"day": "tuesday"}),
    ToolCallCompleted(
        call_id="call_1",
        tool="lookup_slot",
        result_preview="09:00, 13:00",
        result_size=12,
        result_sha256="c0ffee" * 10 + "abcd",
        artifact_id=None,
        error=None,
    ),
    NodeUpdated(node="shout", state_patch={"text": "HELLO"}),
    ArtifactCreated(artifact_id="art_1", media_type="text/csv", uri="file:///runs/run_1/art_1.csv", size=2048),
    UsageReported(model="fake-golden", usage=USAGE),
    InputAppended(
        input=[TextBlock(text="make it 10am"), ResourceBlock(uri="s3://cal/tue.ics", media_type="text/calendar")],
        source="operator",
    ),
    Custom(name="langgraph.checkpoint_written", data={"thread_id": "t-1"}),
    # Appended rather than filed with the payloads they belong beside: ``seq`` is this tuple's
    # index, so inserting mid-list rewrites every later snapshot for no schema reason. Appending
    # is not free either — two PRs appending at once shift each other's indices, which is what
    # moved the two reporting snapshots below when #112 landed first (#121 decouples the two).
    ControlRequested(verb="cancel", reason="operator pressed cancel"),
    ControlObserved(verb="cancel", safe_point="tool_dispatch"),
    StatusReported(message="Searching GitHub"),
    ProgressReported(step="Reviewing issues", current=2, total=4),
)


def _event(payload, seq: int) -> Event:
    """Envelope kind is taken from the payload, so a typo can't create a mismatch here."""
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id="run_1",
        session_id="sess_1",
        tenant="acme",
        origin="Greeter",
        ts=TS,
        payload=payload,
    )


EXAMPLES: dict[str, Event] = {p.kind: _event(p, seq) for seq, p in enumerate(PAYLOADS)}


@pytest.fixture(scope="session")
def examples() -> dict[str, Event]:
    return EXAMPLES


@pytest.fixture(scope="session")
def make_event():
    """Envelope around any payload — for the invariant tests, which need many seqs."""
    return _event
