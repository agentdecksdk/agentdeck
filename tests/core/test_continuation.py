"""Which play an ``Executor.execute`` call is, read off the log.

The whole reason there is one executor method and not two: what used to be a separate ``resume``
argument list  -  the answer, the thread it lands on  -  is recorded before the call, so the log is
the only thing that can say it. Every executor branches on this, so a wrong answer here is a run
replayed from its input when it should have continued, or a stranger's checkpoint reused.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentdeck.core.content import DataBlock, TextBlock, answer_of, as_answer
from agentdeck.core.events import (
    Event,
    KnownPayload,
    RunCompleted,
    RunInterrupted,
    RunPaused,
    RunResumed,
    RunStarted,
    TextDelta,
    Usage,
)
from agentdeck.core.status import Continuation, Play, continuation_of

TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
MINE = "r-1"
STRANGER = "r-0"


def _log(*rows: tuple[str, KnownPayload]) -> list[Event]:
    """One session's log, each row tagged with the run it belongs to."""
    return [
        Event(
            kind=payload.kind,
            seq=seq,
            run_id=run_id,
            session_id="s-1",
            namespace="acme",
            origin="Approver",
            ts=TS,
            payload=payload,
        )
        for seq, (run_id, payload) in enumerate(rows)
    ]


def _started() -> RunStarted:
    return RunStarted(invocable="Approver", kind_of_invocable="workflow", input=[TextBlock(text="hi")])


def _interrupted(thread_id: str = "t-1") -> RunInterrupted:
    return RunInterrupted(interrupt_id="i-1", reason="human", payload={"question": "ok?"}, thread_id=thread_id)


def test_a_run_with_no_history_at_all_is_a_fresh_play() -> None:
    assert continuation_of([], MINE) == Continuation(Play.FRESH)


def test_a_run_that_has_only_started_is_a_fresh_play() -> None:
    """``run.started`` is written before the executor is called, so it is always in the history
    of a fresh play and must never read as a continuation."""
    assert continuation_of(_log((MINE, _started())), MINE).play is Play.FRESH


def test_a_lifted_pause_is_a_replay() -> None:
    history = _log(
        (MINE, _started()),
        (MINE, TextDelta(message_id="m1", text="hi")),
        (MINE, RunPaused(reason="operator stepped away")),
        (MINE, RunResumed()),
    )
    assert continuation_of(history, MINE) == Continuation(Play.REPLAY)


def test_an_answered_interrupt_carries_the_value_and_the_thread() -> None:
    """Both halves of what the old ``resume(spec, thread_id, value, ctx)`` took as arguments,
    recovered from the two events that replaced them."""
    history = _log((MINE, _started()), (MINE, _interrupted("t-7")), (MINE, RunResumed(value=as_answer("approved"))))

    assert continuation_of(history, MINE) == Continuation(Play.ANSWER, answer="approved", thread_id="t-7")


def test_an_answer_survives_the_round_trip_through_the_log() -> None:
    """A structured answer is what a graph resumes with, so it has to come back as the object it
    went in as, not as the blocks that carried it."""
    for value in ("yes", {"decision": "approved", "score": 0.9}, [1, 2, 3], 7, True, [], None):
        assert answer_of(as_answer(value)) == value


def test_content_a_caller_handed_in_comes_back_as_content() -> None:
    """The one shape that is not unwrapped: more than one block was content when it arrived, so
    it is content when it is read back."""
    blocks = [TextBlock(text="see this"), DataBlock(data={"ref": 1})]
    assert answer_of(as_answer(blocks)) == blocks


def test_a_lone_text_block_comes_back_as_its_string_and_that_is_the_ceiling() -> None:
    """The pair is not injective, and this is the whole of where: ``"hi"`` and
    ``[TextBlock(text="hi")]`` encode to the same log entry, so both read back as ``"hi"``.

    Pinned rather than fixed. Distinguishing them would mean a wrapper block whose only job is to
    say which of two indistinguishable answers a caller meant, and the executor reading the answer
    cannot act on the difference anyway.
    """
    assert answer_of(as_answer([TextBlock(text="hi")])) == "hi"
    assert as_answer([TextBlock(text="hi")]) == as_answer("hi")


def test_another_runs_tail_in_the_same_session_log_is_not_this_runs_continuation() -> None:
    """``history`` is the whole session log, and a session's previous run can have been abandoned
    mid-pause. Unfiltered, that stranger's ``[run.paused, run.resumed]`` reads as this run
    continuing, and a genuinely fresh run's own input is silently discarded for someone else's
    checkpoint."""
    history = _log(
        (STRANGER, _started()),
        (STRANGER, RunPaused(reason="abandoned")),
        (STRANGER, RunResumed()),
        (MINE, _started()),
    )
    assert continuation_of(history, MINE).play is Play.FRESH
    assert continuation_of(history, STRANGER).play is Play.REPLAY


def test_a_new_run_after_a_completed_one_is_fresh() -> None:
    """The ordinary second turn of a conversation: the session log ends on a terminal event, and
    nothing about it says continue."""
    history = _log(
        (STRANGER, _started()),
        (STRANGER, RunCompleted(output=[TextBlock(text="done")], usage=Usage(input_tokens=1, output_tokens=1))),
        (MINE, _started()),
    )
    assert continuation_of(history, MINE).play is Play.FRESH


def test_an_interrupt_that_was_never_answered_is_not_a_continuation() -> None:
    """A parked run is not being played at all. Only the ``run.resumed`` that a claim writes
    makes the tail a continuation, which is what keeps a reader of a suspended log from
    concluding the run is under way."""
    history = _log((MINE, _started()), (MINE, _interrupted()))
    assert continuation_of(history, MINE).play is Play.FRESH
