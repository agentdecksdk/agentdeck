"""The reference consumer reading the control lifecycle — and its default case, which is the
forward-compatibility promise every consumer makes.

A chat reader watching a stream go quiet cannot tell "the operator pressed cancel" from "the
model is thinking", which is the difference the two control phases exist to show. What the
renderer must never do is stop on a kind it doesn't know, so an unknown one is fed in too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agentdeck.core.events import (
    ControlObserved,
    ControlRequested,
    Event,
    MessageCompleted,
    RunCancelled,
    parse_event,
)
from agentdeck.surfaces.cli.chat import render

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from agentdeck.core.events import KnownPayload

TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _event(payload: KnownPayload, seq: int) -> Event:
    return Event(
        kind=payload.kind,
        seq=seq,
        run_id="r-1",
        session_id="s-1",
        tenant="acme",
        origin="Greeter",
        ts=TS,
        payload=payload,
    )


async def _sse(*events: Event) -> AsyncIterator[str]:
    for event in events:
        yield f"data: {event.model_dump_json()}\n\n"


async def test_both_control_phases_are_reported_with_the_safe_point(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await render(
        _sse(
            _event(ControlRequested(verb="cancel", reason="operator"), 0),
            _event(ControlObserved(verb="cancel", safe_point="tool_dispatch"), 1),
            _event(RunCancelled(reason="operator"), 2),
        )
    )

    assert capsys.readouterr().out.splitlines() == [
        "[control] cancel requested",
        "[control] cancel observed at tool_dispatch",
        "-- run.cancelled --",
    ]


async def test_a_kind_this_renderer_does_not_know_is_skipped_and_the_rest_still_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ``case _`` default, still doing its job now that two arms were added above it."""
    known = _event(MessageCompleted(message_id="m1", text="done"), 1)
    wire = {**known.model_dump(mode="json"), "kind": "run.teleported", "payload": {"kind": "run.teleported"}}
    unknown = parse_event(wire)

    await render(_sse(unknown, known))

    assert capsys.readouterr().out.splitlines() == ["Greeter [m1]: done"]
