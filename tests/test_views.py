"""Views: composable predicates over the event stream, and which kind each built-in selects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

from agentdeck import views
from agentdeck.core.events import KNOWN_KINDS, Event, KnownPayload

TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


_PAYLOAD_BY_KIND = {p.model_fields["kind"].default: p for p in get_args(get_args(KnownPayload)[0])}

_MINIMAL_FIELDS: dict[str, dict[str, object]] = {
    "run.started": {"invocable": "Greeter", "kind_of_invocable": "agent", "input": []},
    "run.completed": {"output": [], "usage": {"input_tokens": 0, "output_tokens": 0}},
    "run.failed": {"error_code": "engine_error", "message": "boom", "retryable": False},
    "run.interrupted": {"interrupt_id": "i-1", "reason": "human", "payload": {}},
    "control.requested": {"verb": "pause"},
    "control.observed": {"verb": "pause", "safe_point": "tool_dispatch"},
    "text.delta": {"message_id": "m-1", "text": "hi"},
    "thought.delta": {"message_id": "m-1", "text": "hi"},
    "message.completed": {"message_id": "m-1", "text": "hi"},
    "tool.call.started": {"call_id": "c-1", "tool": "search", "args": {}},
    "tool.call.completed": {
        "call_id": "c-1",
        "tool": "search",
        "result_preview": "ok",
        "result_size": 2,
        "result_sha256": "0" * 64,
    },
    "node.updated": {"node": "n1", "state_patch": {}},
    "artifact.created": {"artifact_id": "a-1", "media_type": "text/plain", "uri": "file:///x", "size": 1},
    "usage.reported": {"model": "gpt", "usage": {"input_tokens": 1, "output_tokens": 1}},
    "input.appended": {"input": [], "source": "human"},
    "answer.refused": {"reason": "not one of the options"},
    "agent.changed": {"previous_agent": "BookingAgent", "next_agent": "CancelAgent"},
    "report": {"message": "hi"},
    "custom": {"name": "engine.thing", "data": {}},
}


def _event(kind: str) -> Event:
    return Event(
        kind=kind,
        seq=0,
        run_id="r-1",
        session_id="s-1",
        namespace="acme",
        origin="Greeter",
        ts=TS,
        payload=_PAYLOAD_BY_KIND[kind](**_MINIMAL_FIELDS.get(kind, {})),
    )


# --- composition --------------------------------------------------------------------------------


def test_or_matches_either_sides_kind():
    combined = views.chat | views.tools
    assert combined.matches(_event("text.delta"))
    assert combined.matches(_event("tool.call.started"))
    assert not combined.matches(_event("report"))


def test_and_matches_only_when_both_sides_do():
    always = views.all & views.chat
    assert always.matches(_event("text.delta"))
    assert not always.matches(_event("report"))


def test_invert_flips_the_predicate():
    not_chat = ~views.chat
    assert not not_chat.matches(_event("text.delta"))
    assert not_chat.matches(_event("report"))


def test_composition_builds_a_new_view_and_leaves_the_originals_alone():
    combined = views.chat | views.reports
    assert combined.matches(_event("report"))
    assert not views.chat.matches(_event("report"))


# --- the built-ins, one row per KNOWN_KINDS member ----------------------------------------------


BUILT_INS = {
    "chat": {"text.delta", "thought.delta", "message.completed", "agent.changed"},
    "tools": {"tool.call.started", "tool.call.completed"},
    "reports": {"report"},
    "lifecycle": {
        "run.started",
        "run.completed",
        "run.failed",
        "run.paused",
        "run.resumed",
        "run.cancelled",
        "run.interrupted",
    },
    "errors": {"run.failed", "answer.refused"},
    "usage": {"usage.reported"},
}

# Kinds no named view selects  -  reachable only through `views.all`, by design. Pinned as a
# literal, not derived from KNOWN_KINDS: a derived set is true by construction and could never
# catch a future kind falling out of every named view unnoticed.
ALL_ONLY = {"control.requested", "control.observed", "node.updated", "artifact.created", "input.appended", "custom"}


def test_every_known_kind_is_reachable_by_a_named_view_or_deliberately_by_all_only():
    named = set().union(*BUILT_INS.values())
    assert named | ALL_ONLY == KNOWN_KINDS


def test_each_built_in_selects_exactly_its_own_kinds_and_nothing_else():
    for name, kinds in BUILT_INS.items():
        view = getattr(views, name)
        for kind in KNOWN_KINDS:
            assert view.matches(_event(kind)) is (kind in kinds), f"views.{name} on {kind}"


def test_all_only_kinds_are_unreachable_by_any_named_view():
    for kind in ALL_ONLY:
        event = _event(kind)
        for name in BUILT_INS:
            assert not getattr(views, name).matches(event), f"views.{name} should not match {kind}"
        assert views.all.matches(event)


def test_lifecycle_reuses_the_run_status_kinds_rather_than_a_second_list():
    from agentdeck.core.status import LIFECYCLE_KINDS

    assert BUILT_INS["lifecycle"] == LIFECYCLE_KINDS
