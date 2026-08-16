"""Run control's mechanics: where a signal is honored, how often the gate asks, and what a
resume actually replays.

Two things are deliberately not asserted anywhere here. **Timing:** the gate's read bound is
driven by an injected clock and asserted as a read *count*, never as an elapsed duration; where
a signal has to land mid-stream, the scripted model's own hold/release events put the run
exactly there instead of a sleep. **What another engine would do:** the cross-engine contract
(pause suspends, resume continues, cancel is terminal, a late signal is a no-op) lives in
``tests/contract/test_control.py``. This file is the openai-agents adapter and the Runtime.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from agents import Agent, function_tool
from event_log_checks import check_contiguous, check_terminal
from never_yields import NeverYields

# The contract suite's model, reused on purpose: it decides from the *tail* of its input, so a
# replayed turn whose tool result never reached the session asks for that tool again — which is
# what makes the replay cost of a pause observable here.
from openai_agents_cases import TailScriptedModel

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.engines.openai_agents import OpenAIAgentsEngine
from agentdeck.adapters.engines.stub import StubEngine, stub_spec
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.authoring.tools import compile_tool
from agentdeck.core.content import coerce_input
from agentdeck.core.context import Context, RunContext  # noqa: TC001 — ``peek`` resolves it at runtime
from agentdeck.core.control import CONTROL_POLL_INTERVAL, ControlSignal, Gate, RunPausedError, Signal
from agentdeck.core.events import RunCompleted, TextDelta, Usage
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.status import RunStatus, status_of
from agentdeck.errors import SessionBusyError
from agentdeck.runtime.service import Runtime
from agentdeck.testing import ScriptedModel

if TYPE_CHECKING:
    from agentdeck.core.events import Event
    from agentdeck.core.ports import ControlPort, EventStorePort


class Calendar:
    """An application object a run is handed — the subject of the two resupply tests below."""


class CountingControlPort(MemoryControlPort):
    """A control port that says how many times it was read — which is the whole subject of
    issue #85, and unmeasurable from the outside otherwise."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def poll(self, id: str) -> ControlSignal | None:
        self.reads += 1
        return await super().poll(id)


class FakeClock:
    """A monotonic clock a test advances by hand, so a cooldown is arithmetic, not a wait."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _ctx(run_id: str = "r-1", session_id: str | None = "s-1") -> RunContext:
    return RunContext(namespace="acme", run_id=run_id, session_id=session_id)


def _kinds(events: list[Event]) -> list[str]:
    return [event.kind for event in events]


# --- the gate's read bound (#85) ----------------------------------------------------------


async def test_the_first_checkpoint_of_a_run_always_reads_control() -> None:
    """A signal recorded before the run opened has to be honored at the very first safe point:
    an interval the gate had not started counting yet must not swallow it."""
    control = CountingControlPort()
    await control.signal("r-1", Signal.PAUSE)
    gate = Gate(control, "r-1", clock=FakeClock())

    with pytest.raises(RunPausedError):
        await gate.checkpoint()
    assert control.reads == 1


async def test_control_reads_are_bounded_by_the_interval_not_by_the_number_of_safe_points() -> None:
    """500 safe points inside one interval cost one read, not 500. The point of the bound: a
    streaming answer's read rate stops being a function of how fast the model emits tokens."""
    control = CountingControlPort()
    clock = FakeClock()
    gate = Gate(control, "r-1", poll_interval=0.2, clock=clock)

    for _ in range(500):
        await gate.checkpoint()
    assert control.reads == 1

    clock.advance(0.2)
    await gate.checkpoint()
    assert control.reads == 2


async def test_a_signal_recorded_inside_an_interval_is_honored_at_the_first_safe_point_after_it() -> None:
    """Latency, never correctness: the pause is not lost, it is noticed one interval later — and
    still at a safe point, which is the guarantee the bound is not allowed to change."""
    control = CountingControlPort()
    clock = FakeClock()
    gate = Gate(control, "r-1", poll_interval=0.2, clock=clock)

    await gate.checkpoint()  # the run's first read: nothing pending yet
    await control.signal("r-1", Signal.PAUSE)
    await gate.checkpoint()  # still inside the interval, so the gate has not asked again

    clock.advance(0.199)
    await gate.checkpoint()
    assert control.reads == 1  # the answer it already has is still fresh

    clock.advance(1.0)
    with pytest.raises(RunPausedError):
        await gate.checkpoint()
    assert control.reads == 2


async def test_the_cooldown_is_a_deadline_and_never_a_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """The liveness law, made executable: a run reusing the answer it already has must not be
    parked while the interval runs out.

    ``asyncio.sleep`` is an error for the duration of this test, so a cooldown written as a wait
    fails here instead of quietly turning every safe point into a stall. Nothing else catches
    that: the read *counts* are identical either way, and an elapsed-time assertion is exactly
    what ``docs/coding-standards.md`` §8 forbids. The interval is an hour to make the point
    unmissable — a waiting implementation would park this run for an hour per safe point.
    """

    async def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("the gate slept: the cooldown is a deadline off the clock, never a wait")

    monkeypatch.setattr(asyncio, "sleep", never)
    control = CountingControlPort()
    clock = FakeClock()
    gate = Gate(control, "r-1", poll_interval=3_600.0, clock=clock)

    await gate.checkpoint()  # the branch that reads
    for _ in range(100):
        await gate.checkpoint()  # the branch that reuses — an hour of them, at no wait
    clock.advance(3_600.0)
    await gate.checkpoint()  # the deadline passed, so it asks again

    assert control.reads == 2


async def test_a_gate_with_no_control_port_never_reads_and_never_raises() -> None:
    """The default: a run nobody wired control for behaves exactly as it did before this
    feature existed, at no cost per safe point."""
    gate = Gate()
    for _ in range(10):
        await gate.checkpoint()


async def test_a_pending_resume_is_not_something_a_running_run_acts_on() -> None:
    """RESUME lifts a pause; it is not an instruction. A live run that reads one carries on, or
    a resumed run would stop dead at the first safe point after the resume that started it."""
    control = CountingControlPort()
    await control.signal("r-1", Signal.RESUME)
    gate = Gate(control, "r-1", poll_interval=0.0, clock=FakeClock())

    await gate.checkpoint()
    await gate.checkpoint()
    assert control.reads == 2  # read every time, and acted on neither


def test_a_negative_poll_interval_is_refused_rather_than_treated_as_zero() -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        Gate(MemoryControlPort(), "r-1", poll_interval=-1.0)


async def test_the_shipped_default_bound_is_the_one_the_docs_state() -> None:
    """The number is user-facing — it is the cancel-latency bound written on the run-control
    page — so it is pinned here rather than left to drift silently.

    Asserted through a gate built the way a run's gate is built (no ``poll_interval`` passed),
    so this also holds the *default* to the constant: a gate that ignored it would still read
    once inside the interval and twice across it, and the count is what says which one it used.
    """
    assert CONTROL_POLL_INTERVAL == 0.2
    control = CountingControlPort()
    clock = FakeClock()
    gate = Gate(control, "r-1", clock=clock)

    await gate.checkpoint()
    clock.advance(CONTROL_POLL_INTERVAL * 0.99)
    await gate.checkpoint()
    assert control.reads == 1  # still inside the default interval

    clock.advance(CONTROL_POLL_INTERVAL)
    await gate.checkpoint()
    assert control.reads == 2


async def test_a_long_answer_pays_one_read_per_interval_where_it_used_to_pay_one_per_item() -> None:
    """#85's before and after: a 400-chunk answer arriving at a real model's pace (~30ms a
    chunk, so 12 seconds of streaming) costs 400 control reads at the old rate and 58 at the
    shipped bound — the same run, the same safe points, one read in seven.

    Measured at the gate rather than through a Runtime because the gate is the only thing that
    reads: the count is a function of safe points, elapsed time and the interval, and nothing a
    run does around them changes it.
    """
    items = 400
    per_item = 0.03

    async def _reads(poll_interval: float) -> int:
        control = CountingControlPort()
        clock = FakeClock()
        gate = Gate(control, "r-1", poll_interval=poll_interval, clock=clock)
        for _ in range(items):
            await gate.checkpoint()
            clock.advance(per_item)
        return control.reads

    before = await _reads(0.0)
    after = await _reads(CONTROL_POLL_INTERVAL)

    assert before == items  # one read per safe point, which is what #85 measured as the problem
    assert after == 58  # the first read, plus one per 200ms of the 12 seconds that follow
    assert after < before / 6


# --- the openai-agents adapter's safe points ----------------------------------------------


def _agent_runtime(
    model: ScriptedModel, control: ControlPort, *, tools: list[Any] | None = None, store: EventStorePort | None = None
) -> tuple[Runtime, EventStorePort]:
    agent = Agent(name="Chatty", instructions="reply", model=model, tools=tools or [])
    spec = InvocableSpec(name="Chatty", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)
    store = store or MemoryEventStore()
    runtime = Runtime([OpenAIAgentsEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)
    return runtime, store


async def test_a_pause_signalled_mid_stream_lands_after_the_chunk_that_was_in_flight() -> None:
    """ "At the next safe point, never mid-token": the delta being streamed when the pause
    arrived is recorded whole, and the pause follows it.

    The run is held at exactly that point by the model's own event rather than by a sleep, so
    what this asserts is the ordering, not a race that happened to go this way.
    """
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one ", "two ", "three"), hold=hold)
    control = MemoryControlPort()
    runtime, store = _agent_runtime(model, control)
    ctx = _ctx()

    # The real, minted id is read off ``run.started`` before the engine is ever entered: an
    # async generator does not advance between one ``anext`` and the next, so nothing between
    # here and the signal below can let the run reach a safe point first.
    stream = runtime.run("Chatty", coerce_input("hi"), session_id=ctx.session_id, namespace=ctx.namespace)
    started = await anext(stream)

    async def consume() -> list[Event]:
        return [started, *[event async for event in stream]]

    consumer = asyncio.create_task(consume())
    await model.holding.wait()  # the turn is parked after its first delta
    await control.signal(started.run_id, Signal.PAUSE, "operator")
    hold.set()
    events = await consumer

    assert _kinds(events) == [
        "run.started",
        "text.delta",
        "control.requested",
        "control.observed",
        "run.paused",
    ], _kinds(events)
    assert [event.payload.text for event in events if event.kind == "text.delta"] == ["one "]
    assert model.calls == 1  # no further model step was taken
    assert status_of(await store.read(ctx.log_key, ctx)) is RunStatus.PAUSED


async def test_a_pause_during_a_tool_call_waits_for_the_call_to_return() -> None:
    """A non-cancellable tool is not interrupted: it runs to completion, and only then does the
    run stop — before the model step its result would have fed.

    The pause is signalled from *inside* the tool, which is the one window that cannot be
    arranged from outside: the run is between two safe points, inside code the platform does not
    control. Nothing is force-killed, so "pause" here means "after this call, before the next
    step" exactly as documented.
    """
    calls: list[str] = []
    control = MemoryControlPort()
    ctx = _ctx()
    # The tool has no injected context (it is the SDK's own ``function_tool``, not
    # ``compile_tool``), so a closure is the only channel it has to the run's real, minted id.
    # Filled in below, before the run reaches the tool call this signals from inside.
    run_id_holder: list[str] = []

    @function_tool
    async def slow_lookup() -> str:
        """Look something up, slowly."""
        if not calls:
            await control.signal(run_id_holder[0], Signal.PAUSE, "asked while a tool was running")
        calls.append("slow_lookup")
        return "damaged"

    model = TailScriptedModel("it was damaged", tool_name="slow_lookup")
    runtime, store = _agent_runtime(model, control, tools=[slow_lookup])

    stream = runtime.run("Chatty", coerce_input("what happened"), session_id=ctx.session_id, namespace=ctx.namespace)
    started = await anext(stream)
    run_id_holder.append(started.run_id)
    paused = [started, *[event async for event in stream]]
    kinds = _kinds(paused)

    assert calls == ["slow_lookup"]  # the call returned rather than being killed mid-flight
    assert kinds[-1] == "run.paused", kinds
    assert kinds.index("tool.call.started") < kinds.index("control.observed"), kinds
    assert "message.completed" not in kinds, kinds  # the step the tool's result fed was not taken
    assert status_of(paused) is RunStatus.PAUSED

    resumed = [event async for event in runtime.resume_run(run_id_holder[0], namespace=ctx.namespace)]

    # The cost of a pause with no stack to return to, asserted rather than described: the turn
    # replays, so the tool is called a second time. This is why the safe-point contract tells
    # tool authors to tolerate it, and why `ctx.idempotency_key` exists.
    assert calls == ["slow_lookup", "slow_lookup"]
    assert _kinds(resumed)[-1] == "run.completed"
    assert check_contiguous(await store.read(ctx.log_key, ctx)) == []


async def test_resuming_a_paused_turn_replays_it_and_completes_the_run() -> None:
    """A paused turn left no stack to return to, so resume plays it again: the model is asked a
    second time and the run reaches ``run.completed`` under its original ``run_id``.

    The replay is visible in the log — the deltas appear on both sides of the pause. That is the
    documented cost of a pause here, and the reason a tool with side effects has to tolerate
    running twice; a test that hid it would be hiding the contract.
    """
    hold = asyncio.Event()
    model = ScriptedModel(deltas=("one ", "two"), hold=hold)
    control = MemoryControlPort()
    runtime, store = _agent_runtime(model, control)
    ctx = _ctx()

    stream = runtime.run("Chatty", coerce_input("hi"), session_id=ctx.session_id, namespace=ctx.namespace)
    started = await anext(stream)

    async def consume() -> list[Event]:
        return [started, *[event async for event in stream]]

    consumer = asyncio.create_task(consume())
    await model.holding.wait()
    await control.signal(started.run_id, Signal.PAUSE)
    hold.set()
    paused = await consumer

    hold.set()  # the replayed turn must not stall on the pause fixture's own gate
    resumed = [event async for event in runtime.resume_run(started.run_id, namespace=ctx.namespace)]
    log = await store.read(ctx.log_key, ctx)

    assert _kinds(paused)[-1] == "run.paused"
    assert _kinds(resumed)[0] == "run.resumed"
    assert _kinds(log)[-1] == "run.completed"
    assert model.calls == 2  # the turn was played again, not continued from a stack
    assert len([event for event in log if event.kind == "text.delta"]) > 1  # the replay is in the log
    assert check_terminal(log) is None
    assert check_contiguous(log) == []
    # The pause is *taken*, not papered over with a RESUME sentinel: the gate that honored it
    # consumed it, so the replayed turn meets an empty port and does not stop again at its own
    # first safe point. An empty port is also what makes a cancel arriving later legible.
    assert await control.poll(started.run_id) is None


async def test_lifting_a_pause_resupplies_the_run_s_application_context() -> None:
    """``resume_run`` mints a fresh ``RunContext``, and used to mint it with no ``data=`` at all
    — so a run paused mid-tool replayed with its application context gone.

    Undetectable from inside: the context is never serialized, so nothing in the log can be
    compared against what should have been there. A tool written defensively as ``if ctx.data:``
    would have degraded in silence. This asserts the second pass sees the very same object.
    """
    calendar = Calendar()
    seen: list[Any] = []
    control = MemoryControlPort()
    ctx = _ctx()

    async def peek(environment: Context[Calendar]) -> str:
        """Look at the run's environment."""
        seen.append(environment.data)
        if len(seen) == 1:
            # The tool's own injected context already carries the run's real, minted id, so no
            # closure or captured id is needed: the id it addresses is its own.
            await control.signal(environment.run_id, Signal.PAUSE, "asked while a tool was running")
        return "looked"

    model = TailScriptedModel("done", tool_name="peek")
    runtime, _ = _agent_runtime(model, control, tools=[compile_tool(peek)])

    paused = [
        event
        async for event in runtime.run(
            "Chatty",
            coerce_input("what happened"),
            context=calendar,
            session_id=ctx.session_id,
            namespace=ctx.namespace,
        )
    ]
    run_id = paused[0].run_id
    resumed = [event async for event in runtime.resume_run(run_id, context=calendar, namespace=ctx.namespace)]

    assert _kinds(paused)[-1] == "run.paused"
    assert _kinds(resumed)[-1] == "run.completed"
    # The replay called the tool a second time; both calls held the caller's own object.
    assert seen == [calendar, calendar]
    assert seen[1] is calendar


async def test_lifting_a_pause_without_a_context_replays_with_none() -> None:
    """The other half of "resupplied, never recovered": omitting it is not "keep what the run
    had", because the run's value was never written down for anyone to keep."""
    seen: list[Any] = []
    control = MemoryControlPort()
    ctx = _ctx()

    async def peek(environment: Context[Calendar]) -> str:
        """Look at the run's environment."""
        seen.append(environment.data)
        if len(seen) == 1:
            await control.signal(environment.run_id, Signal.PAUSE, "asked while a tool was running")
        return "looked"

    runtime, _ = _agent_runtime(TailScriptedModel("done", tool_name="peek"), control, tools=[compile_tool(peek)])

    events = [
        event
        async for event in runtime.run(
            "Chatty",
            coerce_input("what happened"),
            context=Calendar(),
            session_id=ctx.session_id,
            namespace=ctx.namespace,
        )
    ]
    async for _ in runtime.resume_run(events[0].run_id, namespace=ctx.namespace):
        pass

    assert seen[0] is not None
    assert seen[1] is None


# --- the Runtime's answers -----------------------------------------------------------------


async def test_a_signal_is_honored_with_a_store_that_never_yields() -> None:
    """Liveness is self-supplied: the run path is never parked on a control read, a cooldown or
    a store's good manners. Wrapped in the store that hands the loop no turn at all, a cancel
    still lands and the run still closes."""
    control = MemoryControlPort()
    ctx = _ctx()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="one "),
        TextDelta(message_id="m-1", text="two "),
        RunCompleted(output=coerce_input("one two"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = NeverYields(MemoryEventStore())
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)

    # The real id is read off ``run.started``, then the cancel is recorded before the stream is
    # asked for anything else — still ahead of the engine's first checkpoint, which is what used
    # to be meant by "before it even opened".
    stream = runtime.run("Chatty", coerce_input("hi"), session_id=ctx.session_id, namespace=ctx.namespace)
    started = await anext(stream)
    await control.signal(started.run_id, Signal.CANCEL, "before it even opened")
    events = [started, *[event async for event in stream]]

    assert _kinds(events)[-1] == "run.cancelled"
    assert check_terminal(events) is None


async def test_a_signal_without_a_control_port_says_it_was_not_recorded() -> None:
    """The one answer a caller has to act on: this Runtime cannot control anything, so the
    request went nowhere. Silence here would be a pause button that does nothing."""
    runtime = Runtime([StubEngine()], MemoryEventStore(), {})

    assert await runtime.signal("r-1", Signal.PAUSE) is False


async def test_resuming_a_run_that_is_not_paused_is_a_noop() -> None:
    """Three shapes of the same non-answer — a run still going, a run that finished, and an id
    nobody has heard of — none of which is an error to raise over."""
    control = MemoryControlPort()
    spec = stub_spec("Chatty", RunCompleted(output=coerce_input("done"), usage=Usage(input_tokens=0, output_tokens=0)))
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)
    ctx = _ctx()

    completed = [
        event
        async for event in runtime.run("Chatty", coerce_input("hi"), session_id=ctx.session_id, namespace=ctx.namespace)
    ]
    run_id = completed[0].run_id
    before = await store.read(ctx.log_key, ctx)

    assert [event async for event in runtime.resume_run(run_id, namespace=ctx.namespace)] == []
    assert [event async for event in runtime.resume_run("never-heard-of-it", namespace=ctx.namespace)] == []
    assert await store.read(ctx.log_key, ctx) == before
    assert _kinds(completed)[-1] == "run.completed"


async def test_a_paused_run_keeps_holding_its_session_so_no_second_turn_starts_on_it() -> None:
    """A pause suspends a turn, it does not end it: the conversation stays claimed, and a turn
    asked for meanwhile is refused rather than interleaved with the one that is coming back."""
    control = MemoryControlPort()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="one "),
        RunCompleted(output=coerce_input("one"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)
    ctx = _ctx()

    # Signalled right after the real id is known and before the stream is asked for anything
    # else, so the pause is still recorded ahead of the run's own first safe point.
    stream = runtime.run("Chatty", coerce_input("hi"), session_id=ctx.session_id, namespace=ctx.namespace)
    started = await anext(stream)
    await control.signal(started.run_id, Signal.PAUSE)
    paused = [started, *[event async for event in stream]]

    assert _kinds(paused)[-1] == "run.paused"
    with pytest.raises(SessionBusyError):
        second = runtime.run("Chatty", coerce_input("hi again"), session_id=ctx.session_id, namespace=ctx.namespace)
        await anext(second)


# --- namespace isolation (#315) ---------------------------------------------------------


async def test_two_namespaces_sharing_one_key_pause_and_resume_independently() -> None:
    """#315/#324: both control ports used to key a signal by ``run_id`` alone, so ``acme``'s
    ``order-1234`` and ``globex``'s ``order-1234`` fought over one row: a pause meant for one
    landed on both, and a resume of one could not tell them apart either.

    #324 changes the mechanism, not the intent: ``key`` is no longer the run's address at all,
    so the two namespaces below sharing one key cannot even name the same id to collide over.
    Each gets its own, read straight off its own ``run.started``. The isolation this guards is
    now asserted through that real id rather than a caller-chosen one, the surface a caller
    actually holds (docs/design/run-identity.md).

    Only reachable through the Runtime directly: ``deck.runs.pause``/``resume`` take no
    ``namespace`` (out of scope for #315), while ``deck.runs.cancel`` already has one, which is
    why the cancel side of this collision is proven through ``Deck`` instead
    (``tests/test_deck.py::test_a_cancel_in_one_namespace_leaves_the_same_key_in_another_untouched``).
    """
    control = MemoryControlPort()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="hi"),
        RunCompleted(output=coerce_input("hi"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)

    globex_stream = runtime.run("Chatty", coerce_input("hi"), key="order-1234", namespace="globex")
    globex_started = await anext(globex_stream)
    acme_stream = runtime.run("Chatty", coerce_input("hi"), key="order-1234", namespace="acme")
    acme_started = await anext(acme_stream)

    assert globex_started.run_id != acme_started.run_id  # same key, two unrelated runs

    # Signalled right after acme's real id is known, still ahead of either run's first safe
    # point, the same determinism the contract suite gets from signalling before a run opens.
    assert await runtime.signal(acme_started.run_id, Signal.PAUSE, namespace="acme") is True

    globex = [globex_started, *[event async for event in globex_stream]]
    acme = [acme_started, *[event async for event in acme_stream]]

    assert _kinds(globex)[-1] == "run.completed"  # a different tenant, same key
    assert _kinds(acme)[-1] == "run.paused"  # the run the signal actually named

    resumed_acme = [event async for event in runtime.resume_run(acme_started.run_id, namespace="acme")]
    resumed_globex = [event async for event in runtime.resume_run(globex_started.run_id, namespace="globex")]

    assert _kinds(resumed_acme)[-1] == "run.completed"  # acme's own paused run, lifted
    assert resumed_globex == []  # globex's run already completed, so nothing to resume


async def test_two_namespaces_sharing_one_key_do_not_clobber_each_other_s_signal() -> None:
    """The other half of the same story: acme's pause and globex's cancel are signalled against
    each run's own real id, so there is no shared slot left for one to destroy the other's
    intent, even though both runs were started under the identical caller ``key``."""
    control = MemoryControlPort()
    spec = stub_spec(
        "Chatty",
        TextDelta(message_id="m-1", text="hi"),
        RunCompleted(output=coerce_input("hi"), usage=Usage(input_tokens=0, output_tokens=0)),
    )
    store = MemoryEventStore()
    runtime = Runtime([StubEngine()], store, {"Chatty": spec}, control=control, control_poll_interval=0.0)

    globex_stream = runtime.run("Chatty", coerce_input("hi"), key="order-1234", namespace="globex")
    globex_started = await anext(globex_stream)
    acme_stream = runtime.run("Chatty", coerce_input("hi"), key="order-1234", namespace="acme")
    acme_started = await anext(acme_stream)

    assert await runtime.signal(acme_started.run_id, Signal.PAUSE, namespace="acme") is True
    assert await runtime.signal(globex_started.run_id, Signal.CANCEL, namespace="globex") is True

    globex = [globex_started, *[event async for event in globex_stream]]
    acme = [acme_started, *[event async for event in acme_stream]]

    assert _kinds(globex)[-1] == "run.cancelled"  # its own signal, not acme's pause
    assert _kinds(acme)[-1] == "run.paused"  # its own signal, not overwritten by globex's
