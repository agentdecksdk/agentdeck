"""One contract, two bridges: the ``Context[T]`` a user callable receives must be the same
thing whether the OpenAI Agents SDK or langgraph carried it there.

**One test body per property, parametrized over both engines** — not two similar suites. The
uniformity a user is promised is produced by two adapters that must agree, and two hand-written
suites drift toward whatever each engine happens to do: one bridge handing back a copy, or a
projection, or the internal ``RunContext`` instead of the public view, would keep passing its
own test forever. Here the identical assertion runs against both, so the first bridge to
diverge fails on the other's terms.

``tests/contract/context_subjects.py`` holds everything engine-shaped; nothing below names an
SDK type or a ``StateGraph``. No live model: the openai subject's model is scripted and the
langgraph subject calls no model at all.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from context_subjects import ANSWER, SUBJECTS, Environment, Subject

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.context import Context, RunContext
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from agentdeck.core.events import Event

_READER = RunContext(run_id="reader")
"""Whatever context the store's read side needs — this suite never plays a run with it."""


@pytest.fixture(params=SUBJECTS, ids=lambda build: build().id)
def subject(request: pytest.FixtureRequest) -> Subject:
    """A freshly built root per test, so one test's recorded contexts cannot leak into another."""
    return request.param()


@pytest.fixture
def environment() -> Environment:
    return Environment()


@pytest.fixture
def store() -> MemoryEventStore:
    return MemoryEventStore()


@pytest.fixture
def runtime(subject: Subject, store: MemoryEventStore) -> Runtime:
    return Runtime([subject.engine], store, {subject.spec.name: subject.spec})


async def _play(runtime: Runtime, subject: Subject, context: object) -> list[Event]:
    return [
        event
        async for event in runtime.run(
            subject.spec.name, subject.input, context=context, run_id="r-1", session_id="s-1"
        )
    ]


# --- what the injected callable is handed -------------------------------------------------------


async def test_the_callable_receives_the_public_context_and_not_the_internal_carrier(
    runtime: Runtime, subject: Subject, environment: Environment
) -> None:
    """The one portable type, from both bridges. An adapter that handed its ``RunContext``
    straight through would satisfy every ``ctx.data`` assertion below and still couple every
    tool signature in the application to an AgentDeck internal."""
    await _play(runtime, subject, environment)

    assert [type(seen) for seen in subject.seen] == [Context]


async def test_the_context_data_is_the_callers_own_object_by_reference(
    runtime: Runtime, subject: Subject, environment: Environment
) -> None:
    """Not a copy, not a projection, not a re-validated model — the same object, both engines."""
    await _play(runtime, subject, environment)

    assert len(subject.seen) == 1
    assert subject.seen[0].data is environment


async def test_the_run_identity_travels_with_the_context(
    runtime: Runtime, subject: Subject, environment: Environment
) -> None:
    await _play(runtime, subject, environment)

    assert (subject.seen[0].run_id, subject.seen[0].session_id) == ("r-1", "s-1")


async def test_a_run_given_no_context_reaches_the_callable_with_none(runtime: Runtime, subject: Subject) -> None:
    """``context=`` is optional on both engines, and omitting it is not an error — the callable
    gets the value the application declined to supply."""
    await _play(runtime, subject, None)

    assert subject.seen[0].data is None


# --- the two seams the context carries ------------------------------------------------------------


async def test_the_context_carries_a_working_reporter_and_gate(
    runtime: Runtime, subject: Subject, environment: Environment
) -> None:
    """``ctx.reporter`` and ``ctx.checkpoint()`` are AgentDeck concepts rather than either
    engine's, so both bridges owe them. The subject awaits the gate *before* it reports, so a
    context whose gate was missing never reaches the report this asserts."""
    events = await _play(runtime, subject, environment)

    reported = [event for event in events if event.kind == "status.reported"]
    assert [event.payload.message for event in reported] == [ANSWER]  # ty: ignore[unresolved-attribute]


# --- and what it must never do ----------------------------------------------------------------------


async def test_the_context_is_never_written_to_the_event_log(
    runtime: Runtime, subject: Subject, environment: Environment
) -> None:
    """The lifecycle rule, on both engines: the log records what a run was asked to do, not the
    live objects it held. Asserted by searching for a string only the environment could supply."""
    events = await _play(runtime, subject, environment)

    dumped = json.dumps([event.model_dump(mode="json") for event in events])
    assert "Environment" not in dumped
    assert environment.secret not in dumped


async def test_the_stored_log_holds_no_trace_of_the_context_either(
    runtime: Runtime, subject: Subject, environment: Environment, store: MemoryEventStore
) -> None:
    """Not only what a live consumer saw. A run read back afterwards is the form an auditor or a
    replay gets, and it is the one a leak would survive in."""
    await _play(runtime, subject, environment)

    stored = await store.read_run("s-1", "r-1", _READER)
    dumped = json.dumps([event.model_dump(mode="json") for event in stored])
    assert environment.secret not in dumped
