"""Harness for the contract suite: one Runtime per case, wired to nothing but memory.

The cases themselves live in ``contract_cases.py`` so the test modules can import their types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from case_types import Case, Played
from contract_cases import CASES, TS

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.context import RunContext
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from agentdeck.core.events import Event


@pytest.fixture(params=CASES, ids=lambda case: case.id)
def case(request: pytest.FixtureRequest) -> Case:
    return request.param


@pytest.fixture
def ctx() -> RunContext:
    return RunContext(namespace="acme", run_id="r-1", session_id="s-1")


@pytest.fixture
def store() -> MemoryEventStore:
    return MemoryEventStore()


@pytest.fixture
def runtime(case: Case, store: MemoryEventStore) -> Runtime:
    """One engine, one store, a frozen clock — nothing else in the loop."""
    return Runtime([case.engine], store, {case.spec.name: case.spec}, clock=lambda: TS)


@pytest.fixture
async def played(case: Case, runtime: Runtime, ctx: RunContext) -> Played:
    """Play the case to the end. A raising engine is an outcome under test, not a failure."""
    events: list[Event] = []
    try:
        async for event in runtime.run(
            case.spec.name, case.input, run_id=ctx.run_id, session_id=ctx.session_id, namespace=ctx.namespace
        ):
            events.append(event)
    except Exception as exc:  # the engine's exception is one of the things being asserted about
        return Played(events, exc)
    return Played(events, None)
