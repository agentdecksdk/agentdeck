"""The event schema's contract: round-trip, forward compatibility, and the validators."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from agentdeck.core import (
    KNOWN_KINDS,
    RESULT_PREVIEW_MAX,
    TERMINAL_KINDS,
    ControlObserved,
    ControlRequested,
    ControlVerb,
    Custom,
    DataBlock,
    Event,
    NodeUpdated,
    ProgressReported,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    RunResumed,
    StatusReported,
    TextBlock,
    TextDelta,
    ToolCallCompleted,
    ToolCallStarted,
    UnknownEvent,
    Usage,
    check_contiguous,
    check_terminal,
)
from agentdeck.core.status import LIFECYCLE_KINDS, RunStatus, status_of

TS = "2026-01-01T12:00:00+00:00"


def _wire(kind: str, payload: dict) -> dict:
    """As it arrives off the wire: the payload carries the discriminator too."""
    return {
        "v": 1,
        "kind": kind,
        "seq": 0,
        "run_id": "run_1",
        "session_id": None,
        "namespace": "acme",
        "origin": "Greeter",
        "ts": TS,
        "payload": {"kind": kind, **payload},
    }


def test_every_known_kind_has_an_example(examples):
    assert set(examples) == KNOWN_KINDS


def test_round_trip_every_kind(examples):
    for kind, event in examples.items():
        assert Event.model_validate(json.loads(event.model_dump_json())) == event, kind


def test_terminal_kinds_are_the_documented_three():
    assert {"run.completed", "run.failed", "run.cancelled"} == TERMINAL_KINDS


# --- forward compatibility -------------------------------------------------------------


def test_unknown_kind_parses_as_unknown_event():
    event = Event.model_validate(_wire("future.thing", {"whatever": 1}))
    assert isinstance(event.payload, UnknownEvent)
    assert event.payload.raw_payload == {"kind": "future.thing", "whatever": 1}  # kept raw
    assert event.namespace == "acme" and event.seq == 0  # envelope still fully validated


def test_unknown_field_inside_a_known_payload_is_dropped():
    event = Event.model_validate(_wire("text.delta", {"message_id": "msg_1", "text": "hi", "tone": "cheery"}))
    assert event.payload == TextDelta(message_id="msg_1", text="hi")


def test_unknown_event_survives_its_own_round_trip():
    event = Event.model_validate(_wire("future.thing", {"whatever": 1}))
    assert Event.model_validate(json.loads(event.model_dump_json())) == event  # no double-wrapping


def test_a_consumer_skips_unknown_and_processes_the_rest():
    stream = [
        Event.model_validate(_wire("text.delta", {"message_id": "msg_1", "text": "a"})),
        Event.model_validate(_wire("future.thing", {"whatever": 1})),
        Event.model_validate(_wire("text.delta", {"message_id": "msg_1", "text": "b"})),
    ]
    assert "".join(e.payload.text for e in stream if isinstance(e.payload, TextDelta)) == "ab"
    assert sum(isinstance(e.payload, UnknownEvent) for e in stream) == 1


def test_a_malformed_known_payload_still_raises():
    with pytest.raises(ValidationError):
        Event.model_validate(_wire("text.delta", {"message_id": "msg_1"}))


def test_a_payload_named_raw_payload_does_not_slip_past_its_own_schema():
    """The UnknownEvent arm must not become a bypass for a malformed known payload."""
    with pytest.raises(ValidationError):
        Event.model_validate(_wire("text.delta", {"raw_payload": {"a": 1}}))


def test_an_unknown_payload_keeps_every_sibling_field():
    event = Event.model_validate(_wire("future.thing", {"raw_payload": {"deep": 1}, "extra": 2}))
    assert isinstance(event.payload, UnknownEvent)
    assert event.payload.raw_payload == {
        "kind": "future.thing",
        "raw_payload": {"deep": 1},
        "extra": 2,
    }


def test_a_structured_result_arrives_typed_off_the_wire():
    """The recurrence this shape retires: a validated result, canonical rather than a
    namespaced ``custom`` event with the JSON restated as text."""
    wire = _wire(
        "run.completed",
        {
            "output": [{"type": "data", "data": {"claim_id": "7777", "decision": "approved"}}],
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    )
    event = Event.model_validate(wire)
    assert event.payload == RunCompleted(
        output=[DataBlock(data={"claim_id": "7777", "decision": "approved"})],
        usage=Usage(input_tokens=1, output_tokens=2),
    )
    block = event.payload.output[0]
    assert isinstance(block, DataBlock) and block.data["decision"] == "approved"


def test_an_unknown_field_inside_a_data_block_still_parses():
    wire = _wire(
        "run.started",
        {
            "invocable": "ClaimPipeline",
            "kind_of_invocable": "workflow",
            "parent_run_id": None,
            "input": [{"type": "data", "data": {"input": "claim 7777"}, "encoding": "cbor"}],
            "context": {"trace_id": "t"},
        },
    )
    assert Event.model_validate(wire).payload.input == [DataBlock(data={"input": "claim 7777"})]


def test_unknown_event_refuses_a_known_kind():
    with pytest.raises(ValidationError, match="known kind"):
        UnknownEvent(kind="text.delta", raw_payload={})


def test_a_bad_envelope_raises_even_for_an_unknown_kind():
    wire = _wire("future.thing", {"whatever": 1})
    del wire["namespace"]
    with pytest.raises(ValidationError):
        Event.model_validate(wire)


# --- validators ------------------------------------------------------------------------


def test_envelope_kind_must_match_payload_kind():
    with pytest.raises(ValidationError, match="does not match payload kind"):
        Event(
            kind="text.delta",
            seq=0,
            run_id="run_1",
            session_id=None,
            namespace="acme",
            origin="Greeter",
            ts=TS,
            payload=RunCompleted(
                output=[TextBlock(text="x")],
                usage=Usage(input_tokens=1, output_tokens=2),
            ),
        )


@pytest.mark.parametrize(
    ("envelope", "payload_kind"),
    [
        pytest.param("future.a", "future.b", id="unknown-vs-unknown"),
        pytest.param("future.a", "text.delta", id="unknown-envelope-known-payload"),
    ],
)
def test_two_kinds_that_disagree_are_refused_rather_than_relabelled(envelope, payload_kind):
    """Wrapping an unknown kind used to stamp the envelope's answer over the payload's, so a row
    whose copies disagreed was accepted under a name it never claimed — with its real one buried
    in ``raw_payload``. The disagreement is the whole signal that the row is not what it says."""
    wire = _wire(envelope, {})
    wire["payload"] = {"kind": payload_kind, "message_id": "m", "text": "hi"}
    with pytest.raises(ValidationError, match="does not match payload kind"):
        Event.model_validate(wire)


@pytest.mark.parametrize("kind", ["", "Run Started", "run..started", "run.", ".started", "RUN.STARTED"])
def test_a_kind_that_no_writer_could_emit_is_refused(kind):
    """Shape, not membership: an unfamiliar kind must still parse (that is what ``UnknownEvent``
    is for), but ``kind`` was an open ``str``, so ``""`` and ``"Run Started"`` validated too."""
    with pytest.raises(ValidationError):
        Event.model_validate(_wire(kind, {}))


def test_a_namespace_this_version_has_never_seen_still_parses():
    """The pattern must not become a closed set by accident — digits included, so the A2A and
    MCP surfaces (#129) have somewhere to land."""
    event = Event.model_validate(_wire("a2a.task.started", {"task": "t-1"}))
    assert isinstance(event.payload, UnknownEvent)
    assert event.payload.raw_payload == {"kind": "a2a.task.started", "task": "t-1"}


def test_a_payload_must_carry_its_own_discriminator():
    wire = _wire("text.delta", {"message_id": "msg_1", "text": "hi"})
    del wire["payload"]["kind"]
    with pytest.raises(ValidationError):
        Event.model_validate(wire)


def test_session_id_must_be_stated_even_when_absent():
    wire = _wire("text.delta", {"message_id": "msg_1", "text": "hi"})
    del wire["session_id"]
    with pytest.raises(ValidationError):
        Event.model_validate(wire)


def test_seq_and_counters_reject_negatives():
    wire = _wire("text.delta", {"message_id": "msg_1", "text": "hi"})
    with pytest.raises(ValidationError):
        Event.model_validate({**wire, "seq": -1})
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=0)


def test_events_do_not_mutate(examples):
    event = examples["text.delta"]
    with pytest.raises(ValidationError):
        event.kind = "run.completed"
    with pytest.raises(ValidationError):
        event.payload.text = "rewritten"


def test_result_preview_is_capped():
    args = {"call_id": "call_1", "tool": "t", "result_size": 9, "result_sha256": "a" * 64}
    ToolCallCompleted(result_preview="x" * RESULT_PREVIEW_MAX, **args)
    with pytest.raises(ValidationError, match="string_too_long"):
        ToolCallCompleted(result_preview="x" * (RESULT_PREVIEW_MAX + 1), **args)


@pytest.mark.parametrize("name", ["nodot", ".leading", "trailing.", ""])
def test_custom_name_must_be_namespaced(name):
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        Custom(name=name, data={})


@pytest.mark.parametrize("digest", ["not-a-hash", "", "A" * 64, "a" * 63, "a" * 65])
def test_result_sha256_must_look_like_one(digest):
    """Its neighbour ``result_size`` was ``NonNegativeInt`` while this took any string at all —
    so a truncated or upper-cased digest read as a real one, and the field exists to be
    compared against another."""
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ToolCallCompleted(call_id="c", tool="t", result_preview="x", result_size=1, result_sha256=digest)


def test_run_failed_error_codes_are_a_closed_set():
    RunFailed(error_code="budget_exceeded", message="over", retryable=False)
    with pytest.raises(ValidationError):
        RunFailed(error_code="something_else", message="over", retryable=False)


def test_usage_usd_is_optional_but_tokens_are_not():
    assert Usage(input_tokens=1, output_tokens=2).usd is None
    with pytest.raises(ValidationError):
        Usage(input_tokens=1)


@pytest.mark.parametrize("usd", [float("nan"), float("inf"), float("-inf"), -0.01])
def test_a_cost_json_cannot_carry_is_refused_at_construction(usd):
    """The money fields get the rigour the token counts always had. ``NaN``/``±Infinity`` matter
    most: they serialize as ``null``, so without this a consumer reads *no cost* where the
    producer wrote nonsense — the divergence ``DataBlock`` already refuses for arbitrary data."""
    with pytest.raises(ValidationError):
        Usage(input_tokens=1, output_tokens=2, usd=usd)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="non-finite-float"),
        pytest.param({1, 2, 3}, id="set"),
        pytest.param(datetime(2020, 1, 1, tzinfo=UTC), id="datetime"),
    ],
)
@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda v: NodeUpdated(node="n", state_patch=v), "state_patch"),
        (lambda v: ToolCallStarted(call_id="c", tool="t", args=v), "args"),
        (lambda v: RunInterrupted(interrupt_id="i", reason="human", payload=v), "payload"),
        (lambda v: Custom(name="ns.event", data=v), "data"),
        (lambda v: UnknownEvent(kind="ns.later", raw_payload=v), "raw_payload"),
    ],
)
def test_a_free_form_field_holds_only_what_the_store_hands_back_unchanged(build, field, value):
    """The invariant ``DataBlock`` always had, now on the free-form dicts too. Each of these
    reached the log before and came back as something else — ``nan`` as ``null``, a set as a
    list, a datetime as a string — because the type said ``Any`` and only the adapters cared.

    ``UnknownEvent`` is the one that had to hold it most: it exists so this reader survives a
    newer writer, which it cannot do while it is free to alter that writer's data on the way
    through. ``Custom`` is where a model's own structured output lands, floats included.
    """
    with pytest.raises(ValidationError):
        build({"v": value})


def test_the_free_form_fields_still_take_ordinary_json():
    nested = {"a": [1, "two", None, {"b": 3.5}], "c": True}
    assert NodeUpdated(node="n", state_patch=nested).state_patch == nested
    assert ToolCallStarted(call_id="c", tool="t", args=nested).args == nested
    assert RunInterrupted(interrupt_id="i", reason="human", payload=nested).payload == nested
    assert Custom(name="ns.event", data=nested).data == nested
    assert UnknownEvent(kind="ns.later", raw_payload=nested).raw_payload == nested


# --- status and progress reports (#47) -------------------------------------------------


def test_a_status_message_must_say_something():
    StatusReported(message="Searching GitHub")
    with pytest.raises(ValidationError):
        StatusReported(message="")


def test_a_stage_needs_a_name_but_not_a_count():
    """Named stages alone are valid, per the issue: the counts are the optional half, not the step."""
    assert ProgressReported(step="Reviewing issues").current is None
    assert ProgressReported(step="Reviewing issues", current=3).total is None
    assert ProgressReported(step="Reviewing issues", total=4).current is None
    with pytest.raises(ValidationError):
        ProgressReported(step="")


def test_progress_past_its_own_total_is_refused():
    """The one arithmetic a caller can get wrong, caught at the call rather than in a UI
    rendering "6 of 4"."""
    ProgressReported(step="Reviewing issues", current=4, total=4)
    with pytest.raises(ValidationError, match="past total"):
        ProgressReported(step="Reviewing issues", current=5, total=4)


def test_progress_counts_are_refused_below_their_floors():
    with pytest.raises(ValidationError):
        ProgressReported(step="Reviewing issues", current=-1)
    with pytest.raises(ValidationError):
        ProgressReported(step="Reviewing issues", total=0)  # "2 of 0" is not a count


def test_a_report_moves_nothing_and_terminates_nothing(examples, make_event):
    """The whole reason these are not lifecycle kinds. A run reporting "Searching GitHub" is
    still RUNNING, and a log of nothing but reports has still not ended."""
    reporting = _run(examples, make_event, "run.started", "status.reported", "progress.reported")
    assert status_of(reporting) is RunStatus.RUNNING
    assert check_terminal(reporting) == "no terminal event"

    done = [*reporting, make_event(examples["run.completed"].payload, 3)]
    assert status_of(done) is RunStatus.COMPLETED
    assert check_terminal(done) is None

    assert not {"status.reported", "progress.reported"} & (LIFECYCLE_KINDS | TERMINAL_KINDS)


def test_a_run_that_never_reports_folds_to_the_same_status(examples, make_event):
    """Existing runs behave unchanged when no updates are emitted, as an assertion: the two
    logs differ only by reports, so any status difference would be the reports' doing."""
    quiet = _run(examples, make_event, "run.started", "text.delta", "run.completed")
    noisy = _run(
        examples, make_event, "run.started", "status.reported", "text.delta", "progress.reported", "run.completed"
    )
    assert status_of(quiet) is status_of(noisy)
    assert check_terminal(quiet) is check_terminal(noisy) is None


# --- invariants ------------------------------------------------------------------------


def test_gap_detection_finds_a_planted_gap(examples, make_event):
    delta = examples["text.delta"].payload
    assert check_contiguous(make_event(delta, seq) for seq in (0, 1, 2, 4, 6)) == [3, 5]


def test_contiguous_run_and_empty_run_have_no_gaps(examples, make_event):
    delta = examples["text.delta"].payload
    assert check_contiguous(make_event(delta, seq) for seq in range(4)) == []
    assert check_contiguous([]) == []


def test_gap_detection_refuses_two_runs_at_once(examples, make_event):
    delta = examples["text.delta"].payload
    mixed = [make_event(delta, 0), make_event(delta, 1).model_copy(update={"run_id": "run_2"})]
    with pytest.raises(ValueError, match="one run"):
        check_contiguous(mixed)


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


# --- the control lifecycle -------------------------------------------------------------


@pytest.mark.parametrize("verb", get_args(ControlVerb))
def test_one_pair_of_kinds_carries_every_control_verb(verb):
    """Designed once rather than per verb: pause and steering reuse these two kinds, so they
    need no schema PR of their own — and no reader has to learn a new kind to follow them."""
    requested = Event.model_validate(_wire("control.requested", {"verb": verb}))
    observed = Event.model_validate(_wire("control.observed", {"verb": verb, "safe_point": "stream_item"}))
    assert (requested.payload.verb, observed.payload.verb) == (verb, verb)


def test_a_verb_or_a_safe_point_outside_its_closed_set_is_refused():
    with pytest.raises(ValidationError):
        ControlRequested(verb="teleport")
    with pytest.raises(ValidationError):
        ControlObserved(verb="cancel", safe_point="whenever")


def test_a_request_is_not_a_transition_and_observing_one_is_not_the_effect(examples, make_event):
    """The distinction the lifecycle exists for. A request that counted as a transition would
    report a run cancelled while it was still spending tokens inside a tool call."""
    asked = _run(examples, make_event, "run.started", "control.requested", "control.observed")
    assert status_of(asked) is RunStatus.RUNNING
    assert check_terminal(asked) == "no terminal event"

    stopped = [*asked, make_event(examples["run.cancelled"].payload, 3)]
    assert status_of(stopped) is RunStatus.CANCELLED
    assert check_terminal(stopped) is None

    assert not {"control.requested", "control.observed"} & (LIFECYCLE_KINDS | TERMINAL_KINDS)


def test_an_observation_says_which_safe_point_the_run_was_at(examples):
    """Because "cancel took eight seconds" and "the tool call did" are different answers."""
    observed = examples["control.observed"].payload
    assert (observed.verb, observed.safe_point) == ("cancel", "tool_dispatch")


# --- the resume value ------------------------------------------------------------------


def test_a_resume_answer_is_carried_in_full_not_as_a_preview():
    """Full storage is the whole point: a truncated answer cannot be replayed into an engine
    that never received it, which is the repair this field exists to make possible."""
    answer = {"approved": True, "amount": 240, "note": "x" * 5000}
    event = Event.model_validate(_wire("run.resumed", {"reason": None, "value": [{"type": "data", "data": answer}]}))
    assert event.payload == RunResumed(reason=None, value=[DataBlock(data=answer)])
    block = event.payload.value[0]
    assert isinstance(block, DataBlock) and block.data == answer


def test_a_resume_recorded_before_this_field_existed_still_parses():
    """The compatibility direction a new field is usually tested in the wrong one: a reader
    that demanded a value could not read its own store's older rows."""
    event = Event.model_validate(_wire("run.resumed", {"reason": "approved"}))
    assert event.payload == RunResumed(reason="approved")
    assert event.payload.value is None


def test_a_resume_that_cannot_be_carried_through_returns_the_run_to_waiting(examples, make_event):
    """The stranding half of #94, answered in vocabulary that already exists: status is a fold
    over an append-only log, so recording the interrupt again is the entire rollback."""
    stranded = _run(examples, make_event, "run.started", "run.interrupted", "run.resumed")
    assert status_of(stranded) is RunStatus.RUNNING

    repaired = [*stranded, make_event(examples["run.interrupted"].payload, 3)]
    assert status_of(repaired) is RunStatus.WAITING_HUMAN
    assert check_terminal(repaired) == "no terminal event"
