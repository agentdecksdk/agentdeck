"""The event schema's contract: round-trip, forward compatibility, and the validators."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentdeck.core import (
    KNOWN_KINDS,
    RESULT_PREVIEW_MAX,
    TERMINAL_KINDS,
    Custom,
    Event,
    RunCompleted,
    RunFailed,
    TextBlock,
    TextDelta,
    ToolCallCompleted,
    UnknownEvent,
    Usage,
    check_contiguous,
    check_terminal,
    parse_event,
)

TS = "2026-01-01T12:00:00+00:00"


def _wire(kind: str, payload: dict) -> dict:
    """As it arrives off the wire: the payload carries the discriminator too."""
    return {
        "v": 1,
        "kind": kind,
        "seq": 0,
        "run_id": "run_1",
        "session_id": None,
        "tenant": "acme",
        "origin": "Greeter",
        "ts": TS,
        "payload": {"kind": kind, **payload},
    }


def test_every_known_kind_has_an_example(examples):
    assert set(examples) == KNOWN_KINDS


def test_round_trip_every_kind(examples):
    for kind, event in examples.items():
        assert parse_event(json.loads(event.model_dump_json())) == event, kind


def test_terminal_kinds_are_the_documented_three():
    assert {"run.completed", "run.failed", "run.cancelled"} == TERMINAL_KINDS


# --- forward compatibility -------------------------------------------------------------


def test_unknown_kind_parses_as_unknown_event():
    event = parse_event(_wire("future.thing", {"whatever": 1}))
    assert isinstance(event.payload, UnknownEvent)
    assert event.payload.raw_payload == {"kind": "future.thing", "whatever": 1}  # kept raw
    assert event.tenant == "acme" and event.seq == 0  # envelope still fully validated


def test_unknown_field_inside_a_known_payload_is_dropped():
    event = parse_event(_wire("text.delta", {"message_id": "msg_1", "text": "hi", "tone": "cheery"}))
    assert event.payload == TextDelta(message_id="msg_1", text="hi")


def test_unknown_event_survives_its_own_round_trip():
    event = parse_event(_wire("future.thing", {"whatever": 1}))
    assert parse_event(json.loads(event.model_dump_json())) == event  # no double-wrapping


def test_a_consumer_skips_unknown_and_processes_the_rest():
    stream = [
        parse_event(_wire("text.delta", {"message_id": "msg_1", "text": "a"})),
        parse_event(_wire("future.thing", {"whatever": 1})),
        parse_event(_wire("text.delta", {"message_id": "msg_1", "text": "b"})),
    ]
    assert "".join(e.payload.text for e in stream if isinstance(e.payload, TextDelta)) == "ab"
    assert sum(isinstance(e.payload, UnknownEvent) for e in stream) == 1


def test_a_malformed_known_payload_still_raises():
    with pytest.raises(ValidationError):
        parse_event(_wire("text.delta", {"message_id": "msg_1"}))


def test_a_bad_envelope_raises_even_for_an_unknown_kind():
    wire = _wire("future.thing", {"whatever": 1})
    del wire["tenant"]
    with pytest.raises(ValidationError):
        parse_event(wire)


# --- validators ------------------------------------------------------------------------


def test_envelope_kind_must_match_payload_kind():
    with pytest.raises(ValidationError, match="does not match payload kind"):
        Event(
            kind="text.delta",
            seq=0,
            run_id="run_1",
            tenant="acme",
            origin="Greeter",
            ts=TS,
            payload=RunCompleted(
                output=[TextBlock(text="x")],
                usage=Usage(input_tokens=1, output_tokens=2),
            ),
        )


def test_result_preview_is_capped():
    args = {"call_id": "call_1", "tool": "t", "result_size": 9, "result_sha256": "ab"}
    ToolCallCompleted(result_preview="x" * RESULT_PREVIEW_MAX, **args)
    with pytest.raises(ValidationError, match="RESULT_PREVIEW_MAX"):
        ToolCallCompleted(result_preview="x" * (RESULT_PREVIEW_MAX + 1), **args)


@pytest.mark.parametrize("name", ["nodot", ".leading", "trailing.", ""])
def test_custom_name_must_be_namespaced(name):
    with pytest.raises(ValidationError, match="namespace"):
        Custom(name=name, data={})


def test_run_failed_error_codes_are_a_closed_set():
    RunFailed(error_code="budget_exceeded", message="over", retryable=False)
    with pytest.raises(ValidationError):
        RunFailed(error_code="something_else", message="over", retryable=False)


def test_usage_usd_is_optional_but_tokens_are_not():
    assert Usage(input_tokens=1, output_tokens=2).usd is None
    with pytest.raises(ValidationError):
        Usage(input_tokens=1)


# --- invariants ------------------------------------------------------------------------


def test_gap_detection_finds_a_planted_gap(examples, make_event):
    delta = examples["text.delta"].payload
    assert check_contiguous(make_event(delta, seq) for seq in (0, 1, 2, 4, 6)) == [3, 5]


def test_contiguous_run_and_empty_run_have_no_gaps(examples, make_event):
    delta = examples["text.delta"].payload
    assert check_contiguous(make_event(delta, seq) for seq in range(4)) == []
    assert check_contiguous([]) == []


def _run(examples, make_event, *kinds: str) -> list[Event]:
    return [make_event(examples[kind].payload, seq) for seq, kind in enumerate(kinds)]


def test_terminal_check_accepts_one_terminal_last(examples, make_event):
    assert check_terminal(_run(examples, make_event, "text.delta", "run.completed")) is None


def test_terminal_check_catches_zero_terminals(examples, make_event):
    assert check_terminal(_run(examples, make_event, "text.delta")) == "no terminal event"


def test_terminal_check_catches_two_terminals(examples, make_event):
    violation = check_terminal(_run(examples, make_event, "run.failed", "run.completed"))
    assert violation is not None and "2 terminal events" in violation


def test_terminal_check_catches_terminal_not_last(examples, make_event):
    violation = check_terminal(_run(examples, make_event, "run.completed", "text.delta"))
    assert violation is not None and "not last" in violation


def test_an_interrupt_does_not_terminate_a_run(examples, make_event):
    assert check_terminal(_run(examples, make_event, "run.interrupted")) == "no terminal event"


def test_a_resumed_run_keeps_its_run_id_and_continues_its_seq(examples, make_event):
    run = _run(examples, make_event, "run.started", "run.interrupted", "run.resumed", "run.completed")
    assert {event.run_id for event in run} == {"run_1"}
    assert check_contiguous(run) == []
    assert check_terminal(run) is None
