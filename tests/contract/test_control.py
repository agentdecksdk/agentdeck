"""Run control, held to one contract across engines: what pause, resume and cancel mean is
the platform's answer, not each engine's.

Every case here signals the run *before* it opens, so the first safe point is the one that
honors it — that makes the ordering deterministic without a clock or a sleep anywhere. Where a
signal lands *mid-stream* is pinned separately, in ``tests/test_run_control.py``, off the
scripted model's own hold/release events.

Cases are factories, not built values: control state (a paused thread) lives on the engine
instance itself for langgraph, so a case shared across every test function in this module would
leak one test's pause into the next test's run on the same ``thread_id``. A fresh engine (and
spec) per test closes that off for all three cases alike, langgraph included.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from agents import Agent
from event_log_checks import check_contiguous, check_terminal
from langgraph.graph import END, START, StateGraph

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.engines.langgraph import LangGraphEngine
from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import MessageCompleted, RunCompleted, TextDelta, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.runtime.service import Runtime
from agentdeck.testing import ScriptedModel

if TYPE_CHECKING:
    from agentdeck.core.events import Event, SafePoint
    from agentdeck.core.ports import EnginePort


@dataclass(frozen=True)
class ControlCase:
    """One engine's controllable run: streams a few items, then completes.

    ``safe_point`` is what this engine honors a signal at — the platform's contract is that
    every engine has one, not that they all have the *same* one.
    """

    id: str
    engine: EnginePort
    spec: InvocableSpec
    safe_point: SafePoint = "stream_item"


def _stub_case() -> ControlCase:
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="one "),
        TextDelta(message_id="m-1", text="two "),
        TextDelta(message_id="m-1", text="three"),
        MessageCompleted(message_id="m-1", text="one two three", origin="agent"),
        RunCompleted(output=coerce_input("one two three"), usage=Usage(input_tokens=1, output_tokens=3)),
    )
    return ControlCase(id="stub", engine=StubEngine(), spec=spec)


def _openai_agents_case() -> ControlCase:
    agent = Agent(name="Chatty", instructions="reply", model=ScriptedModel(deltas=("one ", "two ", "three")))
    spec = InvocableSpec(name="Chatty", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)
    return ControlCase(id="openai-agents", engine=OpenAIAgentsEngine(), spec=spec)


class _LangGraphState(TypedDict, total=False):
    input: str
    a: bool
    b: bool
    c: bool


def _langgraph_case() -> ControlCase:
    # Every node ignores whatever state it is handed and returns a fixed patch, so a pause
    # honored mid-graph and a later, unrelated test reusing the same ``thread_id`` (this
    # module's ``ctx`` is the same for every test) can never see each other's leftovers —
    # the same "safe to share across every test function" property langgraph_cases.py's
    # nodes are written for.
    graph: StateGraph[Any] = StateGraph(_LangGraphState)
    graph.add_node("a", lambda _state: {"a": True})
    graph.add_node("b", lambda _state: {"b": True})
    graph.add_node("c", lambda _state: {"c": True})
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    graph.add_edge("c", END)
    spec = InvocableSpec(name="Chatty", kind=InvocableKind.WORKFLOW, engine=LangGraphEngine.engine, native=graph)
    return ControlCase(id="langgraph", engine=LangGraphEngine(), spec=spec, safe_point="node_boundary")


CASE_FACTORIES = [_stub_case, _openai_agents_case, _langgraph_case]
CASE_IDS = ["stub", "openai-agents", "langgraph"]


@pytest.fixture(params=CASE_FACTORIES, ids=CASE_IDS)
def case(request: pytest.FixtureRequest) -> ControlCase:
    return request.param()


@dataclass
class Harness:
    """One Runtime wired to control, and the pieces a test needs to look at afterwards."""

    runtime: Runtime
    store: MemoryEventStore
    control: MemoryControlPort
    ctx: RunContext
    name: str

    async def play(self) -> list[Event]:
        return [
            event
            async for event in self.runtime.run(
                self.name,
                coerce_input("say something"),
                run_id=(self.ctx).run_id,
                session_id=(self.ctx).session_id,
                namespace=(self.ctx).namespace,
            )
        ]

    async def resume(self, reason: str | None = None) -> list[Event]:
        return [
            event
            async for event in self.runtime.resume_run(self.ctx.run_id, namespace=self.ctx.namespace, reason=reason)
        ]

    async def log(self) -> list[Event]:
        return await self.store.read(self.ctx.log_key, self.ctx)


@pytest.fixture
def harness(case: ControlCase) -> Harness:
    """Polls control at every safe point: these tests are about *where* a signal is honored,
    and the read bound that decides *when* the gate hears about it is asserted on its own."""
    store = MemoryEventStore()
    control = MemoryControlPort()
    runtime = Runtime(
        [case.engine],
        store,
        {case.spec.name: case.spec},
        control=control,
        control_poll_interval=0.0,
    )
    ctx = RunContext(namespace="acme", run_id="r-control", session_id="s-control")
    return Harness(runtime=runtime, store=store, control=control, ctx=ctx, name=case.spec.name)


def _kinds(events: list[Event]) -> list[str]:
    return [event.kind for event in events]


def _payload(events: list[Event], kind: str) -> Any:
    return next(event.payload for event in events if event.kind == kind)


async def test_a_paused_run_records_the_request_the_observation_and_stops(harness: Harness) -> None:
    """The three phases, in order, and then nothing: a paused run's own log ends at
    ``run.paused``, which is what "emits no further agent steps while paused" means for a
    reader that was not watching."""
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE)

    events = await harness.play()
    kinds = _kinds(events)

    assert kinds[-3:] == ["control.requested", "control.observed", "run.paused"], kinds
    assert kinds.count("run.paused") == 1, kinds
    assert not {"run.completed", "run.failed", "run.cancelled"} & set(kinds), kinds
    assert check_contiguous(events) == []
    assert status_of(events) is RunStatus.PAUSED
    assert await harness.log() == events  # persisted before yielded, so the two agree exactly


async def test_a_pause_request_is_not_a_status_transition(harness: Harness) -> None:
    """A run stays ``RUNNING`` through its own ``control.requested``: only the effect moves the
    needle. Folded off the real log rather than a hand-built one, so a producer that recorded
    the request as a transition would fail here."""
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE)

    events = await harness.play()
    up_to_the_request = events[: _kinds(events).index("control.requested") + 1]

    assert status_of(up_to_the_request) is RunStatus.RUNNING
    assert _kinds(up_to_the_request)[-1] == "control.requested"


async def test_the_safe_point_a_signal_was_honored_at_is_recorded(harness: Harness, case: ControlCase) -> None:
    """``safe_point`` is what tells "cancel took eight seconds" from "a tool call did": every
    engine honors this signal at its own safe point and says so."""
    await harness.control.signal(harness.ctx.run_id, Signal.CANCEL)

    events = await harness.play()
    observed = _payload(events, "control.observed")

    assert (observed.verb, observed.safe_point) == ("cancel", case.safe_point)


async def test_the_reason_travels_from_the_request_to_the_effect(harness: Harness) -> None:
    """One string, recorded twice on purpose: on the request, so the log says who asked and
    why, and on the effect, so a reader of the terminal event alone still has it."""
    await harness.control.signal(harness.ctx.run_id, Signal.CANCEL, "the user closed the tab")

    events = await harness.play()

    assert _payload(events, "control.requested").reason == "the user closed the tab"
    assert _payload(events, "run.cancelled").reason == "the user closed the tab"


async def test_a_resumed_run_continues_the_same_run_and_completes_it(harness: Harness) -> None:
    """Same ``run_id``, ``seq`` carrying on from the pause, one terminal event at the end of
    the whole log — the pause is a seam in one run, not two runs."""
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE)
    paused = await harness.play()

    resumed = await harness.resume("operator lifted the pause")
    log = await harness.log()

    assert _kinds(resumed)[0] == "run.resumed"
    assert _payload(resumed, "run.resumed").reason == "operator lifted the pause"
    assert {event.run_id for event in log} == {harness.ctx.run_id}
    assert resumed[0].seq == paused[-1].seq + 1
    assert check_contiguous(log) == []
    assert check_terminal(log) is None
    assert _kinds(log)[-1] == "run.completed"
    assert status_of(log) is RunStatus.COMPLETED


async def test_a_cancelled_run_is_terminal_and_cannot_be_resumed(harness: Harness) -> None:
    """Cancel is the one verb with no way back: a resume against it writes nothing at all,
    which is also what keeps a terminal event the run's last event."""
    await harness.control.signal(harness.ctx.run_id, Signal.CANCEL)
    cancelled = await harness.play()
    before = await harness.log()

    resumed = await harness.resume()

    assert _kinds(cancelled)[-1] == "run.cancelled"
    assert status_of(cancelled) is RunStatus.CANCELLED
    assert resumed == []
    assert await harness.log() == before


async def test_cancelling_a_paused_run_ends_it_rather_than_resuming_it(harness: Harness) -> None:
    """The abandoned-pause path: pause, think, give up. A paused run has no loop polling the
    gate, so nothing turns that cancel into an effect on its own — the next resume attempt is
    the only thing that will ever look, and it must honor the cancel instead of playing the run
    on. Otherwise the request is silently dropped and the run completes as if nobody asked.

    No ``control.observed`` here, unlike a cancel served at a safe point: this run reached none.
    """
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE)
    await harness.play()
    await harness.runtime.signal(harness.ctx.run_id, Signal.CANCEL, "user closed the tab")

    resumed = await harness.resume()
    log = await harness.log()

    assert _kinds(resumed) == ["run.resumed", "control.requested", "run.cancelled"]
    assert _payload(resumed, "control.requested").verb == "cancel"
    assert _payload(resumed, "run.cancelled").reason == "user closed the tab"
    assert status_of(log) is RunStatus.CANCELLED
    assert check_terminal(log) is None
    assert check_contiguous(log) == []
    # No text.delta after the pause: the run was never played on.
    assert _kinds(log).count("run.completed") == 0
    # And cancel stayed terminal — a second attempt finds nothing to resume.
    assert await harness.resume() == []


@pytest.mark.parametrize("verb", [Signal.PAUSE, Signal.CANCEL])
async def test_a_signal_that_lost_the_race_with_a_terminal_event_records_nothing(
    harness: Harness, verb: Signal
) -> None:
    """No ``control.rejected`` to write, and nowhere to write it: a terminal event is a run's
    last event by invariant, so a signal arriving after one is a no-op by construction —
    nothing polls the gate once the loop has exited."""
    completed = await harness.play()
    before = await harness.log()

    assert await harness.runtime.signal(harness.ctx.run_id, verb, "too late") is True
    assert await harness.log() == before
    assert _kinds(completed)[-1] == "run.completed"


async def test_pausing_twice_records_one_request(harness: Harness) -> None:
    """A double-clicked pause is one pending signal, so the run stops once and the log says so
    once — idempotent because the port keeps one signal per run, not a queue of them."""
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE, "first")
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE, "second")

    events = await harness.play()
    kinds = _kinds(events)

    assert kinds.count("control.requested") == 1, kinds
    assert kinds.count("run.paused") == 1, kinds
    assert _payload(events, "control.requested").reason == "second"


async def test_only_one_of_two_concurrent_resumes_continues_a_paused_run(harness: Harness) -> None:
    """Two callers, one paused run: the claim that flips ``PAUSED`` to ``RUNNING`` is the store's
    conditional append, so the loser plays nothing rather than running the turn twice."""
    await harness.control.signal(harness.ctx.run_id, Signal.PAUSE)
    await harness.play()

    first = await harness.resume()
    second = await harness.resume()

    assert _kinds(first)[0] == "run.resumed"
    assert second == []
    assert _kinds(await harness.log()).count("run.resumed") == 1
