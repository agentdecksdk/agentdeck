"""Child runs: what ``ctx.invoke`` starts, and what ``ctx.parallel`` makes of several.

The property every case here is really about is that "child" is a relationship between two
ordinary runs and not a second kind of execution: the handle a body gets back has the id, the
log and the controls a top-level run has, and the only thing the parent holds is the handle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import pytest

from agentdeck import Deck, ToolCtx, WorkflowCtx, tool, workflow
from agentdeck.core.context import RunContext
from agentdeck.core.ports import Observer
from agentdeck.core.status import RunStatus
from agentdeck.errors import ConfigError, NotFoundError, RunSuspendedError


@pytest.fixture(autouse=True)
def _no_project(tmp_path, monkeypatch):
    """A cwd with no ``.agentdeck``: every catalog here is code-first."""
    monkeypatch.chdir(tmp_path)


@tool
async def shout(word: str) -> str:
    """Say it louder."""
    return word.upper()


@workflow
async def joined(ctx: WorkflowCtx, left: str, right: str) -> str:
    return f"{left}+{right}"


@workflow
async def asker(ctx: WorkflowCtx, question: str) -> str:
    return f"{question}:{await ctx.ask(question)}"


@workflow
async def awaiting(ctx: WorkflowCtx, word: str) -> str:
    return str(await ctx.invoke("shout", word))


@tool
def thread_of(word: str) -> str:
    """Report the thread this body ran on."""
    return threading.current_thread().name


@workflow
async def awaiting_sync(ctx: WorkflowCtx, word: str) -> str:
    return str(await ctx.invoke("thread_of", word))


# --- what a child is ----------------------------------------------------------------------


async def test_an_awaited_child_hands_its_result_back_to_the_body() -> None:
    async with Deck(workflows=[shout, awaiting]) as deck:
        assert await deck.run("awaiting", "hi") == "HI"


async def test_a_sync_child_body_runs_off_the_event_loop() -> None:
    """``ctx.invoke`` awaits the body directly, so a sync one has to be threaded the way the
    model-facing path threads it: on the loop it would stall the stream and every safepoint."""
    async with Deck(workflows=[thread_of, awaiting_sync]) as deck:
        assert await deck.run("awaiting_sync", "hi") != threading.current_thread().name


@tool
async def peek(environment: ToolCtx[str], word: str) -> str:
    """A ``@tool`` that asks for the run's application context, the other half of what a plain
    function carrying one is now refused for."""
    return f"{environment.data}:{word}"


@workflow
async def awaiting_context(ctx: WorkflowCtx, word: str) -> str:
    return str(await ctx.invoke("peek", word))


async def test_a_tool_declaring_toolctx_is_injected_through_ctx_invoke_too() -> None:
    """The model-facing path is covered end to end in ``test_tool_compilation.py``; this is the
    other caller ``compile_tool`` has to serve, and the reason ``@tool`` compiles the same
    callable for both rather than each owning a separate bridge."""
    async with Deck(workflows=[peek, awaiting_context], context=str) as deck:
        assert await deck.run("awaiting_context", "hi", context="app") == "app:hi"


async def test_a_held_child_is_a_run_of_its_own() -> None:
    """Its own id, its own log, its own controls  -  and its own session, which is to say none:
    the parent holds the one it was started on, and a second turn against it would be refused."""
    held: dict[str, Any] = {}

    @workflow
    async def holding(ctx: WorkflowCtx) -> str:
        child = ctx.invoke(shout, "quiet")
        held.update(parent=ctx.run_id, child=child.id, session=child.session_id, cancellable=child.can.cancel)
        return str(await child)

    async with Deck(workflows=[shout, holding]) as deck:
        parent = await deck.runs.start("holding", None, session_id="s-1")
        assert await parent == "QUIET"

        assert held["child"] != held["parent"]
        assert held["session"] is None
        assert held["cancellable"]

        child = await deck.runs.get(held["child"])
        assert await child.status() is RunStatus.COMPLETED
        assert [event.kind async for event in child.events()][0] == "run.started"
        assert {event.run_id async for event in child.events()} == {held["child"]}


async def test_invoke_binds_to_the_targets_own_signature() -> None:
    """Four spellings of the same call, because binding is by signature and a run is opened with
    one value: the mapping form is what the two meet through."""

    @workflow
    async def binding(ctx: WorkflowCtx) -> list[str]:
        return [
            str(await ctx.invoke("joined", "a", "b")),
            str(await ctx.invoke(joined, "a", right="b")),
            str(await ctx.invoke("joined", left="a", right="b")),
            str(await ctx.invoke("joined", {"left": "a", "right": "b"})),
        ]

    async with Deck(workflows=[joined, binding]) as deck:
        assert await deck.run("binding", None) == ["a+b"] * 4


async def test_an_argument_given_twice_is_refused_the_way_a_call_would() -> None:
    @workflow
    async def doubling(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(joined, "a", left="b", right="c"))

    async with Deck(workflows=[joined, doubling]) as deck:
        run = await deck.runs.start("doubling", None)
        with pytest.raises(ConfigError, match="twice"):
            await run


async def test_too_many_positional_arguments_name_what_the_target_takes() -> None:
    @workflow
    async def crowding(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(joined, "a", "b", "c"))

    async with Deck(workflows=[joined, crowding]) as deck:
        run = await deck.runs.start("crowding", None)
        with pytest.raises(ConfigError, match="left, right"):
            await run


# --- what invoke will and will not take -----------------------------------------------------


async def test_a_definition_this_deck_does_not_hold_is_refused_by_name() -> None:
    """A run's log records its invocable by name, and that name is what an answer, a resume or a
    cancel resolves back through: a child of a definition nobody registered would run once and
    then be unreachable."""

    @tool
    async def unregistered(word: str) -> str:
        return word

    @workflow
    async def reaching(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(unregistered, "x"))

    async with Deck(workflows=[reaching]) as deck:
        run = await deck.runs.start("reaching", None)
        with pytest.raises(ConfigError, match="does not hold under that name"):
            await run


async def test_a_bare_callable_waits_for_the_invocation_resolver() -> None:
    """One rule and no special case: everything that is not a name or a native definition needs
    the resolver, a plain callable included."""

    @workflow
    async def guessing(ctx: WorkflowCtx) -> str:
        async def plain() -> str:  # pragma: no cover  -  never invoked
            return "no"

        return str(await ctx.invoke(plain))

    async with Deck(workflows=[guessing]) as deck:
        run = await deck.runs.start("guessing", None)
        with pytest.raises(ConfigError, match="invocation resolver"):
            await run


# --- parallel ------------------------------------------------------------------------------


async def test_parallel_returns_its_results_in_the_order_the_runs_were_given() -> None:
    @workflow
    async def both(ctx: WorkflowCtx) -> list[str]:
        return [str(value) for value in await ctx.parallel(ctx.invoke(joined, "a", "b"), ctx.invoke("shout", "c"))]

    async with Deck(workflows=[joined, shout, both]) as deck:
        assert await deck.run("both", None) == ["a+b", "C"]


async def test_the_first_failure_in_parallel_cancels_its_siblings_and_propagates() -> None:
    """All-or-nothing: the body sees the exception itself, not a list of outcomes to remember to
    inspect, and the sibling is told to stop rather than left running behind it."""
    ids: dict[str, str] = {}
    running = asyncio.Event()

    @workflow
    async def lingering(ctx: WorkflowCtx) -> str:
        running.set()
        for _ in range(500):
            await ctx.safepoint()
            await asyncio.sleep(0.01)
        return "never"  # pragma: no cover  -  the cancel arrives first

    @workflow
    async def exploding(ctx: WorkflowCtx) -> str:
        await running.wait()
        raise ZeroDivisionError("the sibling raised")

    @workflow
    async def both(ctx: WorkflowCtx) -> list[str]:
        first, second = ctx.invoke(lingering), ctx.invoke(exploding)
        ids.update(lingering=first.id, exploding=second.id)
        return [str(value) for value in await ctx.parallel(first, second)]

    async with Deck(workflows=[lingering, exploding, both]) as deck:
        run = await deck.runs.start("both", None)
        with pytest.raises(ZeroDivisionError, match="the sibling raised"):
            await run

        sibling = await _child(deck, ids["lingering"])
        await _settles(sibling, RunStatus.CANCELLED)


async def test_parallel_refuses_a_bare_ask_rather_than_orphaning_the_first_question() -> None:
    """agentdeck #414: one run parks on one question at a time, so two ``ask`` coroutines awaited
    at once would leave the first waiting on a future nobody can complete. ``parallel`` composes
    child runs, each of which parks on its own channel, and refuses anything else by name."""

    @workflow
    async def gathering(ctx: WorkflowCtx) -> list[str]:
        return [str(value) for value in await ctx.parallel(ctx.ask("a?"), ctx.ask("b?"))]

    async with Deck(workflows=[gathering]) as deck:
        run = await deck.runs.start("gathering", None)
        with pytest.raises(TypeError, match="one question at a time"):
            await run
        assert await run.status() is RunStatus.FAILED


async def test_a_refused_parallel_still_gives_up_the_children_it_had_already_started() -> None:
    """``ctx.invoke`` starts its run at the call, so by the time ``parallel`` can refuse one of
    its arguments the others are already executing. Refusing without cancelling them is the
    all-or-nothing rule broken in the one place nothing failed."""
    ids: list[str] = []
    running = asyncio.Event()

    @workflow
    async def lingering(ctx: WorkflowCtx) -> str:
        running.set()
        for _ in range(500):
            await ctx.safepoint()
            await asyncio.sleep(0.01)
        return "never"  # pragma: no cover  -  the cancel arrives first

    @workflow
    async def mixing(ctx: WorkflowCtx) -> list[str]:
        child = ctx.invoke(lingering)
        ids.append(child.id)
        await running.wait()
        return [str(value) for value in await ctx.parallel(child, ctx.ask("q?"))]

    async with Deck(workflows=[lingering, mixing]) as deck:
        run = await deck.runs.start("mixing", None)
        with pytest.raises(TypeError, match="one question at a time"):
            await run

        orphan = await _child(deck, ids[0])
        await _settles(orphan, RunStatus.CANCELLED)


async def test_giving_up_a_parallel_leaves_the_child_that_is_waiting_for_an_answer() -> None:
    """A run is not its own sibling, and one waiting for an answer is not running behind anybody:
    it holds the only state still worth acting on, and the ``RunSuspendedError`` this raises names
    it. Cancelling it would make that message a lie."""
    ids: dict[str, str] = {}
    running = asyncio.Event()

    @workflow
    async def lingering(ctx: WorkflowCtx) -> str:
        running.set()
        for _ in range(500):
            await ctx.safepoint()
            await asyncio.sleep(0.01)
        return "never"  # pragma: no cover  -  the cancel arrives first

    @workflow
    async def mixed(ctx: WorkflowCtx) -> list[str]:
        waiting, busy = ctx.invoke("asker", "still there?"), ctx.invoke(lingering)
        ids.update(waiting=waiting.id, busy=busy.id)
        await running.wait()
        return [str(value) for value in await ctx.parallel(waiting, busy)]

    async with Deck(workflows=[asker, lingering, mixed]) as deck:
        run = await deck.runs.start("mixed", None)
        with pytest.raises(RunSuspendedError):
            await run

        busy = await _child(deck, ids["busy"])
        await _settles(busy, RunStatus.CANCELLED)

        waiting = await _child(deck, ids["waiting"])
        assert await waiting.status() is RunStatus.WAITING_ANSWER
        await waiting.answer("yes")
        assert await waiting == "still there?:yes"


async def test_giving_up_a_parallel_cancels_a_child_that_is_merely_paused() -> None:
    """Only ``WAITING_ANSWER`` is spared. A paused child has nobody holding a reason to resume it
    once the parent has failed, so sparing it would leave a run that only a staleness sweep ends."""
    ids: list[str] = []
    running = asyncio.Event()

    @workflow
    async def lingering(ctx: WorkflowCtx) -> str:
        running.set()
        for _ in range(500):
            await ctx.safepoint()
            await asyncio.sleep(0.01)
        return "never"  # pragma: no cover  -  the pause, then the cancel, arrive first

    @workflow
    async def parking(ctx: WorkflowCtx) -> list[str]:
        held = ctx.invoke(lingering)
        ids.append(held.id)
        await running.wait()
        await held.pause("the parent parked it")
        for _ in range(500):
            if await held.status() is RunStatus.PAUSED:
                break
            await asyncio.sleep(0.01)
        return [str(value) for value in await ctx.parallel(held, ctx.ask("q?"))]

    async with Deck(workflows=[lingering, parking]) as deck:
        run = await deck.runs.start("parking", None)
        with pytest.raises(TypeError, match="one question at a time"):
            await run

        paused = await _child(deck, ids[0])
        await _settles(paused, RunStatus.CANCELLED)


async def test_a_child_that_fails_to_tear_down_does_not_replace_the_error_being_raised(caplog) -> None:
    """Teardown may not outrank the diagnosis it is tearing down for: whatever the giving-up path
    trips over, the caller keeps the message that names the problem and says what to do. Not
    silently, though  -  a child that could not be told to stop is still running, and this is the
    only thing left to say so."""

    class Wedged:
        """A child-run shape whose store is gone. Stubbed because a real run cannot be made to
        fail this way on demand, and what is asserted is the real error reaching the real caller."""

        id = "r-wedged"

        async def status(self) -> RunStatus:
            raise RuntimeError("the store is gone")

        async def cancel(self, reason: str | None = None) -> None:
            raise RuntimeError("the store is gone")  # pragma: no cover  -  status raises first

    @workflow
    async def wedged(ctx: WorkflowCtx) -> list[str]:
        return [str(value) for value in await ctx.parallel(Wedged(), ctx.ask("q?"))]

    async with Deck(workflows=[wedged]) as deck:
        run = await deck.runs.start("wedged", None)
        with (
            caplog.at_level(logging.WARNING, logger="agentdeck.core.context"),
            pytest.raises(TypeError, match="one question at a time"),
        ):
            await run
        assert "r-wedged" in caplog.text


async def test_parallel_refuses_an_asyncio_task_rather_than_taking_it_for_a_run() -> None:
    """A ``Task`` has ``cancel`` and would pass a duck test, then fail to answer the questions the
    giving-up path asks a child run."""

    @workflow
    async def tasking(ctx: WorkflowCtx) -> list[str]:
        return [str(value) for value in await ctx.parallel(asyncio.get_running_loop().create_future())]

    async with Deck(workflows=[tasking]) as deck:
        run = await deck.runs.start("tasking", None)
        with pytest.raises(TypeError, match="takes the child runs"):
            await run


# --- a child that asks -----------------------------------------------------------------------


async def test_two_children_that_ask_at_once_each_keep_their_own_question() -> None:
    """The other half of #414's answer: two questions asked concurrently through child runs are
    two runs parked on two channels, so neither park replaces the other and each is answered on
    its own."""
    ids: list[str] = []

    @workflow
    async def two(ctx: WorkflowCtx) -> list[str]:
        ids.extend(child.id for child in (ctx.invoke("asker", "a?"), ctx.invoke("asker", "b?")))
        return list(ids)

    async with Deck(workflows=[asker, two]) as deck:
        assert await deck.run("two", None) == ids

        children = [await _child(deck, id) for id in ids]
        for child in children:
            await _settles(child, RunStatus.WAITING_ANSWER)
        assert [(await child.pending() or {})["payload"]["question"] for child in children] == ["a?", "b?"]

        for child, answer in zip(children, ["yes", "no"], strict=True):
            await child.answer(answer)
        assert [await child for child in children] == ["a?:yes", "b?:no"]


async def test_awaiting_a_child_that_asks_raises_and_leaves_it_answerable() -> None:
    """A child's question is not the parent's to wait out: awaiting one raises where a top-level
    ``await run`` raises (docs/design/run-identity.md §15) rather than blocking the body on
    somebody eventually answering. The child stays waiting, and is answered from outside."""
    ids: list[str] = []

    @workflow
    async def impatient(ctx: WorkflowCtx) -> str:
        child = ctx.invoke("asker", "ready?")
        ids.append(child.id)
        return str(await child)

    async with Deck(workflows=[asker, impatient]) as deck:
        run = await deck.runs.start("impatient", None)
        with pytest.raises(RunSuspendedError):
            await run

        child = await _child(deck, ids[0])
        assert await child.status() is RunStatus.WAITING_ANSWER
        await child.answer("yes")
        assert await child == "ready?:yes"


async def _child(deck: Deck, run_id: str) -> Any:
    """A handle on a child by id. ``ctx.invoke`` returns before the opening claim lands, so a
    lookup from outside can be the first to ask."""
    for _ in range(200):
        try:
            return await deck.runs.get(run_id)
        except NotFoundError:
            await asyncio.sleep(0.01)
    raise AssertionError(f"child run {run_id} never opened")


async def _settles(run: Any, status: RunStatus) -> None:
    """Wait for the run to reach ``status``. The body runs in its own task, so a test that
    asserted immediately would be racing it."""
    for _ in range(200):
        if await run.status() is status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run.id} never reached {status}, last seen {await run.status()}")


class _Buckets(Observer):
    """A consumer bucketing the stream by conversation, which is what #491 was reported from."""

    def __init__(self) -> None:
        self.seen: dict[str | None, list[str]] = {}

    async def emit(self, event: Any) -> None:
        self.seen.setdefault(event.session_id, []).append(event.kind)


async def test_a_childs_events_name_the_conversation_its_parent_was_started_on() -> None:
    """#491: the child's whole lifecycle used to arrive with no session at all, so a consumer
    bucketing by conversation dropped it into a process-wide bucket shared with every other
    tenant's children. The parent's own claim on the session is untouched, which the child
    opening at all proves: a turn on that session would be refused for it."""
    buckets = _Buckets()

    @workflow
    async def delegating(ctx: WorkflowCtx, word: str) -> str:
        return str(await ctx.invoke(shout, word))

    async with Deck(workflows=[shout, delegating], observers=[buckets]) as deck:
        assert await deck.run("delegating", "quiet", session_id="s-1") == "QUIET"

    assert None not in buckets.seen
    assert buckets.seen["s-1"].count("run.started") == 2
    assert buckets.seen["s-1"].count("run.completed") == 2


async def test_attributing_a_child_leaves_the_conversations_own_log_alone() -> None:
    """Attribution, not participation: the child is named on its events and nowhere else, so the
    conversation's log  -  which is the transcript an executor is played with and the set a claim
    scans  -  holds the parent's turn and only that."""

    @workflow
    async def delegating(ctx: WorkflowCtx, word: str) -> str:
        return str(await ctx.invoke(shout, word))

    async with Deck(workflows=[shout, delegating]) as deck:
        parent = await deck.runs.start("delegating", "quiet", session_id="s-1")
        assert await parent == "QUIET"

        logged = await deck._runtime.store.read_session(RunContext(run_id="reader", session_id="s-1"))
        assert {event.run_id for event in logged} == {parent.id}


async def test_an_answered_childs_later_events_are_attributed_too() -> None:
    """The half a single-segment run never reaches: a child that suspends is answered from
    outside, and the events of that second segment name the same conversation as its first."""
    buckets = _Buckets()
    ids: list[str] = []

    @workflow
    async def impatient(ctx: WorkflowCtx) -> str:
        child = ctx.invoke("asker", "ready?")
        ids.append(child.id)
        return str(await child)

    async with Deck(workflows=[asker, impatient], observers=[buckets]) as deck:
        run = await deck.runs.start("impatient", None, session_id="s-1")
        with pytest.raises(RunSuspendedError):
            await run

        child = await _child(deck, ids[0])
        await child.answer("yes")
        assert await child == "ready?:yes"

    assert None not in buckets.seen
    assert "run.resumed" in buckets.seen["s-1"]
