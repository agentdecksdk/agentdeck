"""Every cell of the two lifecycle tables, written out.

``test_vocabularies_agree.py`` pins that the tables are *total*; this pins what they actually
say. The two are different failures: a missing cell is a request nothing reads, a wrong cell is
a request read and answered backwards, and only the second one can flip an operator's stop into
an approval.

Spelled out rather than derived from the tables under test, which would pass whatever they said.
``docs/design/run-lifecycle.md`` is the source these were copied from; if a cell here disagrees
with that file, one of the two is wrong and neither should be edited to match the code.
"""

from __future__ import annotations

import pytest

from agentdeck.core.status import (
    POLICY,
    PRECONDITIONS,
    Action,
    Controls,
    Operation,
    RunStatus,
    Verdict,
    can_of,
    decide,
    status_of,
)

_TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

# (state, pending verb) -> (action, whether the intent is taken). Sixteen written cells, over
# six states once the terminal row is read for each of the three.
ROUTING: dict[tuple[RunStatus, str | None], tuple[Action, bool]] = {
    (RunStatus.RUNNING, "cancel"): (Action.HALT, True),
    (RunStatus.RUNNING, "pause"): (Action.HALT, True),
    (RunStatus.RUNNING, "resume"): (Action.PROCEED, False),
    (RunStatus.RUNNING, None): (Action.PROCEED, False),
    (RunStatus.PAUSED, "cancel"): (Action.TERMINATE, True),
    (RunStatus.PAUSED, "pause"): (Action.PROCEED, True),
    (RunStatus.PAUSED, "resume"): (Action.PROCEED, True),
    (RunStatus.PAUSED, None): (Action.PROCEED, False),
    (RunStatus.WAITING_ANSWER, "cancel"): (Action.TERMINATE, True),
    (RunStatus.WAITING_ANSWER, "pause"): (Action.REFUSE, False),
    (RunStatus.WAITING_ANSWER, "resume"): (Action.PROCEED, True),
    (RunStatus.WAITING_ANSWER, None): (Action.PROCEED, False),
    **{(state, verb): (Action.NO_OP, True) for state in _TERMINAL for verb in ("cancel", "pause", "resume")},
    **{(state, None): (Action.NO_OP, False) for state in _TERMINAL},
}

LEGALITY: dict[tuple[RunStatus, Operation], Verdict] = {
    (RunStatus.RUNNING, Operation.RUN): Verdict.REFUSED,
    (RunStatus.RUNNING, Operation.ANSWER): Verdict.REFUSED,
    (RunStatus.RUNNING, Operation.RESUME): Verdict.NO_OP,
    (RunStatus.PAUSED, Operation.RUN): Verdict.REFUSED,
    (RunStatus.PAUSED, Operation.ANSWER): Verdict.REFUSED,
    (RunStatus.PAUSED, Operation.RESUME): Verdict.LEGAL,
    (RunStatus.WAITING_ANSWER, Operation.RUN): Verdict.REFUSED,
    (RunStatus.WAITING_ANSWER, Operation.ANSWER): Verdict.LEGAL,
    (RunStatus.WAITING_ANSWER, Operation.RESUME): Verdict.REFUSED,
    (RunStatus.RUNNING, Operation.PAUSE): Verdict.LEGAL,
    (RunStatus.RUNNING, Operation.CANCEL): Verdict.LEGAL,
    (RunStatus.PAUSED, Operation.PAUSE): Verdict.NO_OP,
    (RunStatus.PAUSED, Operation.CANCEL): Verdict.LEGAL,
    # Legal, and not a pause of running work: it is the veto the routing table honours, holding
    # the answer back until somebody lifts it.
    (RunStatus.WAITING_ANSWER, Operation.PAUSE): Verdict.LEGAL,
    (RunStatus.WAITING_ANSWER, Operation.CANCEL): Verdict.LEGAL,
    **{(state, Operation.RUN): Verdict.LEGAL for state in _TERMINAL},
    **{(state, Operation.ANSWER): Verdict.NO_OP for state in _TERMINAL},
    **{(state, Operation.RESUME): Verdict.NO_OP for state in _TERMINAL},
    **{(state, Operation.PAUSE): Verdict.NO_OP for state in _TERMINAL},
    **{(state, Operation.CANCEL): Verdict.NO_OP for state in _TERMINAL},
}

# What ``run.can`` says for each (engine capability x state), written out the same way. The
# `suspendable=False` column is not a hypothetical: it is what a target with no safe point to
# reach gets, and the whole point of deriving `can` from two inputs rather than one.
CONTROLS: dict[tuple[bool, RunStatus], tuple[bool, bool, bool]] = {
    (True, RunStatus.RUNNING): (True, False, True),
    (True, RunStatus.PAUSED): (False, True, True),
    (True, RunStatus.WAITING_ANSWER): (True, False, True),
    (False, RunStatus.RUNNING): (False, False, True),
    (False, RunStatus.PAUSED): (False, False, True),
    (False, RunStatus.WAITING_ANSWER): (False, False, True),
    **{(suspendable, state): (False, False, False) for suspendable in (True, False) for state in _TERMINAL},
}


@pytest.mark.parametrize(("cell", "expected"), sorted(ROUTING.items(), key=repr))
def test_each_routing_cell_says_what_the_design_says_it_says(
    cell: tuple[RunStatus, str | None], expected: tuple[Action, bool]
) -> None:
    state, verb = cell
    ruling = decide(state, verb)
    assert (ruling.action, ruling.consume) == expected


@pytest.mark.parametrize(("cell", "expected"), sorted(LEGALITY.items(), key=repr))
def test_each_precondition_cell_says_what_the_design_says_it_says(
    cell: tuple[RunStatus, Operation], expected: Verdict
) -> None:
    assert PRECONDITIONS[cell].verdict is expected


@pytest.mark.parametrize(("cell", "expected"), sorted(CONTROLS.items(), key=repr))
def test_each_control_cell_says_what_the_design_says_it_says(
    cell: tuple[bool, RunStatus], expected: tuple[bool, bool, bool]
) -> None:
    suspendable, state = cell
    controls = can_of(state, suspendable=suspendable)
    assert (controls.pause, controls.resume, controls.cancel) == expected


def test_a_run_that_cannot_suspend_can_still_be_cancelled() -> None:
    """The reason cancel asks only the state: ending a run needs no safe point to come back
    from, so the lowest-capability target still gets the one control that stops it."""
    assert can_of(RunStatus.RUNNING, suspendable=False) == Controls(pause=False, resume=False, cancel=True)


def test_no_ruling_is_silent() -> None:
    """The invariant the table exists for: every read of the control port ends in an event or an
    explicit no-op. ``Action`` has no member meaning "do nothing and say nothing", so this holds
    by construction  -  what it guards is somebody adding one."""
    assert {ruling.action for ruling in POLICY.values()} <= set(Action)
    assert all(ruling.why for ruling in POLICY.values())


def test_a_cancel_against_a_stopped_run_is_terminal_rather_than_deferred() -> None:
    """The design's first opinion, and #229's fix. ``signal()`` used to defer a cancel to the
    next resume because nothing claimed the run on the cancel path; the routing claims first, so
    the race that forced the deferral is gone and both suspended states end the run at once."""
    for state in (RunStatus.PAUSED, RunStatus.WAITING_ANSWER):
        assert decide(state, "cancel").action is Action.TERMINATE


def test_a_pause_against_a_waiting_run_refuses_the_answer_and_keeps_both_intents() -> None:
    """The design's second opinion, and the one cell most likely to be "simplified" back into a
    lift. Lifting lets an answer silently override an operator who said stop; refusing costs one
    round trip. ``consume=False`` is the half that keeps the pause pending  -  a refusal that ate
    the intent would lose the stop it just cited."""
    ruling = decide(RunStatus.WAITING_ANSWER, "pause")

    assert ruling.action is Action.REFUSE
    assert ruling.consume is False
    assert "stop" in ruling.why


def test_an_empty_sequence_folds_to_no_status_at_all() -> None:
    """``RunStatus.PENDING`` used to be the fold's identity element, and it named a state no run
    is ever in: ``run.started`` is row 0, so there is no moment between "does not exist" and
    ``RUNNING``. It was declared and never produced, which made every store that returned it
    indistinguishable from one answering about a run it had never seen."""
    assert status_of([]) is None
    assert not hasattr(RunStatus, "PENDING")
    assert len(RunStatus) == 6


def test_a_refusal_names_the_operation_that_would_have_worked() -> None:
    """A caller holding a ``run_id`` off a stream it was watching has no other way to find out
    which of the two verbs this run is waiting for, so the message has to carry it."""
    assert "run.answer(...)" in PRECONDITIONS[RunStatus.WAITING_ANSWER, Operation.RESUME].why
    assert "run.resume()" in PRECONDITIONS[RunStatus.PAUSED, Operation.ANSWER].why
