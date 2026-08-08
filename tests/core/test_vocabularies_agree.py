"""Three value sets are written twice in core, on purpose. These pin the correspondence.

Each pair has a reason to stay separate — a StrEnum a caller branches on, a Literal the schema
validates — but nothing structural makes them agree, so adding a member to one and forgetting
the other is a silent drift. That is what these tests are for; they are the cheap half of the
choice not to merge them.
"""

from __future__ import annotations

from typing import get_args

from agentdeck.core.control import Signal
from agentdeck.core.events import KNOWN_KINDS, TERMINAL_KINDS, ControlVerb, RunStarted
from agentdeck.core.invocable import InvocableKind
from agentdeck.core.status import LIFECYCLE_KINDS, TERMINAL_STATUSES, RunStatus


def test_every_signal_is_a_control_verb_the_schema_can_record():
    """``Signal`` is what a caller may ask for; ``ControlVerb`` is what an event may say. A
    signal the schema cannot record would raise inside the run that honored it."""
    assert {signal.value for signal in Signal} <= set(get_args(ControlVerb))


def test_control_verbs_without_a_signal_are_the_unbuilt_ones():
    """The gap is deliberate and named: ``steer`` is a mailbox, not a signal (see
    ``ports/control.py``). Anything else appearing here is a verb that lost its port."""
    assert set(get_args(ControlVerb)) - {signal.value for signal in Signal} == {"steer"}


def test_invocable_kinds_match_what_run_started_accepts():
    """``InvocableKind`` is what the authoring layer compiles to; ``kind_of_invocable`` is what
    ``run.started`` carries. A kind the schema rejects cannot open a run."""
    literal = get_args(RunStarted.model_fields["kind_of_invocable"].annotation)
    assert {kind.value for kind in InvocableKind} == set(literal)


def test_every_kind_the_lifecycle_tables_name_is_a_kind_the_schema_mints():
    """``_KIND_TO_STATUS`` and ``TERMINAL_KINDS`` are hand-written strings — the one place in
    core where a kind is spelled rather than derived from its payload class.

    A typo does not raise anywhere: the entry simply never matches, so a run's status quietly
    stops advancing past that transition, or ``check_terminal`` quietly stops seeing an end.
    Both failures look like a working system with one missing event.
    """
    assert LIFECYCLE_KINDS <= KNOWN_KINDS
    assert TERMINAL_KINDS <= LIFECYCLE_KINDS


def test_terminal_statuses_are_exactly_the_unresumable_finished_ones():
    """``TERMINAL_STATUSES`` is derived from the kind table now, so this guards the derivation's
    result rather than a hand-written list: the three a run can end on, and no others."""
    assert {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED} == TERMINAL_STATUSES
