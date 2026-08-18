"""Run status, and the lifecycle rules that hang off it  -  derived from the event log's own
transitions, never a second store.

There is no status *table*  -  the run-lifecycle events already are the transitions, so
"persist status" and "persist the log" are the same write. A reader recovers status by
folding a run's events in order, which is what makes it correct after a restart: whatever
the log says is whatever status is, with no cache to go stale.

Four declarations, and nothing else in the tree may write a lifecycle rule of its own
(``docs/design/run-lifecycle.md``): :data:`STATES` says what is true of a state,
:data:`TRANSITIONS` says which event moves it, :data:`PRECONDITIONS` says which operation is
legal in it, and :data:`POLICY` says what a signal found in the control port does when it is
read. This module holds no state of its own  -  delete its memory between two calls and nothing
is lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from agentdeck.core.events import TERMINAL_KINDS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agentdeck.core.events import Event


class RunStatus(StrEnum):
    """A run's lifecycle. ``PAUSED`` and ``WAITING_ANSWER`` resume differently: the first
    with nothing, the second with a value  -  callers must not conflate them.

    There is no member for "no events yet": a run's ``run.started`` is row 0, so there is no
    moment between "does not exist" and ``RUNNING`` for one to name. ``status_of`` returns
    ``None`` there instead.
    """

    RUNNING = "running"
    PAUSED = "paused"
    WAITING_ANSWER = "waiting_answer"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Operation(StrEnum):
    """What a caller *invokes*, which is a different question from what is *pending*.

    ``run`` opens a run, ``answer`` supplies the value an interrupt is waiting for, ``resume``
    lifts a pause. Legality is a property of these; :data:`POLICY`'s columns are the signals
    found in the port at the moment of a read, and conflating the two is what made a pause
    against a waiting run look liftable.
    """

    RUN = "run"
    ANSWER = "answer"
    RESUME = "resume"


@dataclass(frozen=True, slots=True)
class StateFacts:
    """What is true of one state. ``suspended`` means the run has an engine to re-enter and a
    terminal event still owed; ``terminal`` means no outgoing transition and nothing owed."""

    terminal: bool
    suspended: bool


STATES: Mapping[RunStatus, StateFacts] = {
    RunStatus.RUNNING: StateFacts(terminal=False, suspended=False),
    RunStatus.PAUSED: StateFacts(terminal=False, suspended=True),
    RunStatus.WAITING_ANSWER: StateFacts(terminal=False, suspended=True),
    RunStatus.COMPLETED: StateFacts(terminal=True, suspended=False),
    RunStatus.FAILED: StateFacts(terminal=True, suspended=False),
    RunStatus.CANCELLED: StateFacts(terminal=True, suspended=False),
}
"""Which state a suspension gets is decided by how it resumes, not by who caused it: with a
value is ``WAITING_ANSWER``, with nothing is ``PAUSED``. So code pausing itself is ``PAUSED``
rather than a seventh state."""

# Only these kinds move the needle; everything else (deltas, tool calls, node.updated, ...)
# leaves status exactly where it was.
TRANSITIONS: dict[str, RunStatus] = {
    "run.started": RunStatus.RUNNING,
    "run.paused": RunStatus.PAUSED,
    "run.resumed": RunStatus.RUNNING,
    "run.interrupted": RunStatus.WAITING_ANSWER,
    "run.completed": RunStatus.COMPLETED,
    "run.failed": RunStatus.FAILED,
    "run.cancelled": RunStatus.CANCELLED,
}

# A store indexing by kind reads only these to answer "what status is this run"; the derivation
# itself stays in ``status_of``.
LIFECYCLE_KINDS: frozenset[str] = frozenset(TRANSITIONS)

# Derived, not listed again: which statuses are terminal and which kinds are is one fact. A
# terminal kind added to events.py without a transition here raises KeyError at import.
TERMINAL_STATUSES = frozenset(TRANSITIONS[kind] for kind in TERMINAL_KINDS)

# Suspended, not finished: a run in one of these has an engine to re-enter and a terminal
# event still owed. Cancelled is deliberately absent  -  terminal is terminal.
RESUMABLE_STATUSES = frozenset(status for status, facts in STATES.items() if facts.suspended)

# A run ending on one of these is waiting, not finished: its terminal event arrives on resume.
# Derived from the same table the statuses are, so the kind side and the status side of
# "suspended" cannot drift apart the way two hand-written frozensets in two modules did.
SUSPENDED_KINDS = frozenset(kind for kind, status in TRANSITIONS.items() if STATES[status].suspended)


class Verdict(StrEnum):
    """Whether an operation may be attempted in a state at all."""

    LEGAL = "legal"
    REFUSED = "refused"
    NO_OP = "no_op"


@dataclass(frozen=True, slots=True)
class Precondition:
    """One cell of :data:`PRECONDITIONS`. ``why`` is written to be read by whoever was refused,
    so it names the operation that *would* have worked."""

    verdict: Verdict
    why: str


_LEGAL = Precondition(Verdict.LEGAL, "the operation is what this state is waiting for")
_BUSY = Precondition(Verdict.REFUSED, "the session already has a run in flight")

_LEGALITY: Mapping[RunStatus, Mapping[Operation, Precondition]] = {
    RunStatus.RUNNING: {
        Operation.RUN: _BUSY,
        Operation.ANSWER: Precondition(Verdict.REFUSED, "the run is still running and nothing awaits an answer"),
        Operation.RESUME: Precondition(Verdict.NO_OP, "the run is already running, so there is no pause to lift"),
    },
    RunStatus.PAUSED: {
        Operation.RUN: _BUSY,
        Operation.ANSWER: Precondition(
            Verdict.REFUSED, "the run is paused, not waiting for a value: lift it with run.resume()"
        ),
        Operation.RESUME: _LEGAL,
    },
    RunStatus.WAITING_ANSWER: {
        Operation.RUN: _BUSY,
        Operation.ANSWER: _LEGAL,
        Operation.RESUME: Precondition(
            Verdict.REFUSED,
            "the run is waiting for a value, not for a pause to be lifted: supply it with run.answer(...)",
        ),
    },
} | dict.fromkeys(
    TERMINAL_STATUSES,
    {
        Operation.RUN: _LEGAL,
        Operation.ANSWER: Precondition(Verdict.NO_OP, "the run has already ended, so there is nothing to answer"),
        Operation.RESUME: Precondition(Verdict.NO_OP, "the run has already ended, so there is no pause to lift"),
    },
)

# Keyed off ``RunStatus`` and ``Operation`` rather than off the rows, for the reason ``POLICY``
# gives below: a member added without a row fails at import, not at the first call that needs it.
PRECONDITIONS: Mapping[tuple[RunStatus, Operation], Precondition] = {
    (status, operation): _LEGALITY[status][operation] for status in RunStatus for operation in Operation
}
"""Which operation is legal in which state, checked *before* the control port is read.

``run`` is declared here and enforced nowhere: opening a run is refused by ``claim_start``'s
conditional append, and a pre-check in front of it would add exactly the check-then-write race
that claim exists to close. The column is written so the table is the whole truth and a test
can hold the claim to it.
"""


class Action(StrEnum):
    """What a ruling makes happen. Every member is an event or an explicit no-op: the invariant
    this table exists for is that no read of the control port ends in silence."""

    HALT = "halt"
    TERMINATE = "terminate"
    PROCEED = "proceed"
    REFUSE = "refuse"
    NO_OP = "no_op"


@dataclass(frozen=True, slots=True)
class Ruling:
    """What to do, what becomes of the intent, and one sentence of why that doubles as the
    error message and the test name.

    ``consume`` is a compare-and-set through ``ControlPort.consume``, never a blind write: an
    unconditional one would overwrite, and silently destroy, a cancel that arrived while the run
    was suspended  -  the one signal nothing else will ever notice.
    """

    action: Action
    consume: bool
    why: str


_NOTHING_PENDING = Ruling(Action.PROCEED, consume=False, why="nothing is pending")

_RUNNING_ROW: Mapping[str | None, Ruling] = {
    "cancel": Ruling(Action.HALT, consume=True, why="the run was cancelled at a safe point"),
    "pause": Ruling(Action.HALT, consume=True, why="the run was paused at a safe point"),
    # Reading it again next interval costs nothing, and leaving it is what lets the resume that
    # lifted a pause stay legible in the port until something actually acts on it.
    "resume": Ruling(
        Action.PROCEED, consume=False, why="a run that is already running has nothing to do about a resume"
    ),
    None: _NOTHING_PENDING,
}

_PAUSED_ROW: Mapping[str | None, Ruling] = {
    "cancel": Ruling(
        Action.TERMINATE, consume=True, why="a cancel recorded while the run was paused ends it instead of resuming it"
    ),
    "pause": Ruling(
        Action.PROCEED,
        consume=True,
        why="this resume is the answer to that pause, so lifting it is what resuming means",
    ),
    "resume": Ruling(Action.PROCEED, consume=True, why="the pause this resume lifts had already been lifted"),
    None: _NOTHING_PENDING,
}

_WAITING_ANSWER_ROW: Mapping[str | None, Ruling] = {
    "cancel": Ruling(
        Action.TERMINATE, consume=True, why="a cancel recorded while the run waited ends it instead of answering it"
    ),
    # Lifting it would let an answer silently override an operator who said stop. Refusing costs
    # the answerer one round trip and keeps both intents intact.
    "pause": Ruling(
        Action.REFUSE,
        consume=False,
        why="an operator asked this run to stop before the answer arrived, and answering it would override that",
    ),
    "resume": Ruling(Action.PROCEED, consume=True, why="a lifted pause leaves the answer free to land"),
    None: _NOTHING_PENDING,
}

_TERMINAL_ROW: Mapping[str | None, Ruling] = {
    "cancel": Ruling(Action.NO_OP, consume=True, why="the run had already ended when the cancel arrived"),
    "pause": Ruling(Action.NO_OP, consume=True, why="the run had already ended when the pause arrived"),
    "resume": Ruling(Action.NO_OP, consume=True, why="the run had already ended when the resume arrived"),
    None: Ruling(Action.NO_OP, consume=False, why="nothing is pending and the run is over"),
}

_ROUTING: Mapping[RunStatus, Mapping[str | None, Ruling]] = {
    RunStatus.RUNNING: _RUNNING_ROW,
    RunStatus.PAUSED: _PAUSED_ROW,
    RunStatus.WAITING_ANSWER: _WAITING_ANSWER_ROW,
} | dict.fromkeys(TERMINAL_STATUSES, _TERMINAL_ROW)

# Keyed off ``RunStatus`` rather than off the rows, so a member added without a row raises
# ``KeyError`` here, while this module is being imported, rather than the first time a caller
# lands on it. The same reason ``TERMINAL_STATUSES`` derives instead of being listed.
POLICY: Mapping[tuple[RunStatus, str | None], Ruling] = {
    (status, verb): _ROUTING[status][verb] for status in RunStatus for verb in _RUNNING_ROW
}
"""What a pending signal does when it is read, keyed by the state the run was in and the verb
found in the port  -  ``None`` for an empty port.

Read at two moments only: a gate checkpoint while the run is live, and the claim that begins the
operation continuing a stopped one. Four rows of four cells, expanded over the three terminal
statuses so the mapping is total; a state without a row raises KeyError while this module is
being imported rather than answering wrongly at runtime.

The verbs are spelled rather than imported from ``core.control.Signal``, because ``Gate`` reads
this table and importing it back here would be a cycle. ``Signal`` is a ``StrEnum``, so a member
is its own key; ``tests/core/test_vocabularies_agree.py`` pins the two vocabularies together.
"""


def status_of(events: Sequence[Event]) -> RunStatus | None:
    """One run's status: the last transition kind wins, in log order. ``None`` for a run with
    no transition at all  -  one the log has never heard of, or one whose events carry no
    lifecycle kind, which no reader can tell apart."""
    status: RunStatus | None = None
    for event in events:
        status = TRANSITIONS.get(event.kind, status)
    return status


def can_resume(status: RunStatus | None) -> bool:
    """A run is resumable while suspended: waiting on an answer, or paused by an operator.
    Both continue under the same ``run_id``; what differs is what the resume carries.

    Any other status  -  still running, already resumed by a race, terminal, or a run the log has
    never heard of  -  makes a resume a no-op rather than an error, which is why callers check
    this instead of raising."""
    return status in RESUMABLE_STATUSES


def decide(status: RunStatus, pending: str | None) -> Ruling:
    """The one way the runtime reads :data:`POLICY`. Every control-port read in the tree ends
    here, and every ruling ends in an event or an explicit no-op  -  silence cannot be tested,
    logged or seen by a user, which is how three defects survived a release."""
    return POLICY[status, pending]
