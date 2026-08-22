"""What an executor does with each of the three plays, called directly.

``tests/core/test_continuation.py`` pins what the log *says*; this pins what an executor does
about it, without a Runtime in between. The stub is the subject because its script makes "played
from the top" and "played from after the interrupt" two visibly different outputs  -  the real
executors' equivalent is covered against their own runtimes in ``tests/contract/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from plays import answered, lifted

from agentdeck.adapters.executors.stub import StubExecutor, stub_spec
from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.events import MessageCompleted, RunCompleted, RunInterrupted, TextDelta, Usage

if TYPE_CHECKING:
    from agentdeck.core.events import Event, KnownPayload

RUN_ID = "r-1"
PAUSE = RunInterrupted(interrupt_id="i-1", reason="human", payload={"question": "ok?"}, thread_id="t-1")
DONE = RunCompleted(output=[TextBlock(text="done")], usage=Usage(input_tokens=1, output_tokens=1))


def _spec():
    return stub_spec(
        "Asker",
        TextDelta(message_id="m1", text="thinking"),
        PAUSE,
        MessageCompleted(message_id="m1", text="done"),
        DONE,
    )


async def _played(history: list[Event]) -> list[KnownPayload]:
    ctx = RunContext(run_id=RUN_ID, session_id="s-1", namespace="acme")
    return [payload async for payload in StubExecutor().execute(_spec(), coerce_input("hi"), history, ctx)]


async def test_a_fresh_play_runs_to_the_interrupt_and_stops() -> None:
    assert [payload.kind for payload in await _played([])] == ["text.delta", "run.interrupted"]


async def test_an_answered_interrupt_plays_only_what_came_after_it() -> None:
    """The whole point of the collapse: no second method, and the run does not re-do the work it
    already did before it suspended."""
    history = answered(PAUSE, "yes", run_id=RUN_ID, session_id="s-1", origin="Asker")

    assert [payload.kind for payload in await _played(history)] == ["message.completed", "run.completed"]


async def test_a_lifted_pause_plays_from_the_top() -> None:
    """A pause is not an interrupt: a scripted run has no checkpoint to come back to, so lifting
    one replays it from its own input. The declared cost of a safe point
    (``docs/design/run-lifecycle.md``), asserted rather than only written down."""
    history = lifted(run_id=RUN_ID, session_id="s-1", origin="Asker")

    assert [payload.kind for payload in await _played(history)] == ["text.delta", "run.interrupted"]
