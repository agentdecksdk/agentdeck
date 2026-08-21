"""Delegation: an agent hands a bounded task to another agent and gets the result back.

Three forms reach one mechanism. ``Agent(subagents=[...])`` lets the *model* choose, at
inference; ``ctx.agents.create()``/``fork()`` let the *author* mint an agent the catalog does not
hold; both then run through ``ctx.invoke``, which is why nothing here is a second execution path.
What makes a delegated turn more than a tool call is the edge on its ``run.started``: the cost
rolls up along it and a cancel follows it down.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentdeck import Agent, AgentInstance, Deck, ToolCtx, WorkflowCtx, tool, workflow
from agentdeck.core.events import RunStarted
from agentdeck.core.status import RunStatus
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.testing import ScriptedModel, patch_model


@pytest.fixture(autouse=True)
def _no_project(tmp_path, monkeypatch):
    """A cwd with no ``.agentdeck``: every catalog here is code-first."""
    monkeypatch.chdir(tmp_path)


def _writer(**overrides: Any) -> Agent:
    return Agent(name="Writer", instructions="Draft it.", subagents=["Researcher"], **overrides)


def _researcher(**overrides: Any) -> Agent:
    return Agent(name="Researcher", instructions="Find sources.", **overrides)


def _delegating_model(reply: str = "drafted") -> ScriptedModel:
    """One model for the whole tree: the parent's first turn calls the delegation tool, and every
    turn after it (the child's, then the parent's own second) answers in text."""
    return ScriptedModel(deltas=(reply,), tool_name="delegate_to_Researcher", tool_arguments='{"task": "find sources"}')


async def _opening(deck: Deck, run_id: str) -> RunStarted:
    run = await deck.runs.get(run_id)
    return await anext(event.payload async for event in run.events() if isinstance(event.payload, RunStarted))


# --- what the model is given ---------------------------------------------------------------


def test_a_subagent_becomes_one_tool_the_model_can_call() -> None:
    deck = Deck(agents=[_writer(), _researcher()]).build()
    compiled = deck._invocables["Writer"].native  # noqa: SLF001  -  the compiled form is not public

    assert [tool.name for tool in compiled.tools] == ["delegate_to_Researcher"]
    assert "Researcher" in compiled.tools[0].description
    assert "Researcher" not in [handoff.name for handoff in compiled.handoffs]


def test_a_subagents_own_description_is_what_the_model_reads() -> None:
    deck = Deck(agents=[_writer(), _researcher(handoff_description="Searches the literature.")]).build()

    assert deck._invocables["Writer"].native.tools[0].description == "Searches the literature."  # noqa: SLF001


def test_an_unknown_subagent_is_a_build_error_naming_what_there_was() -> None:
    with pytest.raises(NotFoundError, match=r"No agent named 'Nobody'. Available: \['Writer'\]"):
        Deck(agents=[Agent(name="Writer", subagents=["Nobody"])]).build()


def test_an_agent_with_subagents_cannot_be_compiled_without_a_deck() -> None:
    with pytest.raises(ConfigError, match="Put it in Deck"):
        _writer().build()


# --- what a delegated turn is --------------------------------------------------------------


async def test_a_delegated_turn_is_a_child_run_linked_to_its_parent() -> None:
    with patch_model(_delegating_model()):
        async with Deck(agents=[_writer(), _researcher()]) as deck:
            parent = await deck.runs.start("Writer", "write the section")
            await parent

            children = [run.id for run in await deck.runs.list() if run.id != parent.id]
            assert [(await _opening(deck, child)).parent_run_id for child in children] == [parent.id]
            assert [(await _opening(deck, child)).invocable for child in children] == ["Researcher"]
            assert (await _opening(deck, parent.id)).parent_run_id is None


async def test_a_parents_usage_includes_what_it_delegated_and_the_childs_own_stays_readable() -> None:
    with patch_model(_delegating_model()):
        async with Deck(agents=[_writer(), _researcher()]) as deck:
            parent = await deck.runs.start("Writer", "write the section")
            result = await parent

            child_id = next(run.id for run in await deck.runs.list() if run.id != parent.id)
            child = await (await deck.runs.get(child_id))
            assert child.usage.output_tokens > 0
            assert result.usage.output_tokens > child.usage.output_tokens


async def test_a_failed_subagent_reaches_the_parent_rather_than_becoming_an_empty_result() -> None:
    model = _delegating_model()
    model.raises = RuntimeError("the source is down")
    with patch_model(model):
        async with Deck(agents=[_writer(), _researcher()]) as deck:
            parent = await deck.runs.start("Writer", "write the section")
            with pytest.raises(RuntimeError, match="the source is down"):
                await parent

            assert await parent.status() is RunStatus.FAILED


async def test_cancelling_a_parent_cancels_the_child_it_is_waiting_on() -> None:
    started = asyncio.Event()

    @workflow
    async def slow(ctx: WorkflowCtx) -> str:
        started.set()
        for _ in range(3000):
            await ctx.safepoint()
            await asyncio.sleep(0.01)
        return "never"

    @workflow
    async def delegating(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(slow))

    async with Deck(workflows=[slow, delegating]) as deck:
        parent = await deck.runs.start("delegating", None)
        await asyncio.wait_for(started.wait(), timeout=5)
        await parent.cancel("done with it")

        child_id = next(run.id for run in await deck.runs.list() if run.id != parent.id)
        child = await deck.runs.get(child_id)
        await asyncio.wait_for(_settled(child), timeout=5)
        assert await child.status() is RunStatus.CANCELLED


async def _settled(run: Any) -> None:
    while (await run.status()) not in {RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.FAILED}:
        await asyncio.sleep(0.01)


# --- the bounds ----------------------------------------------------------------------------


async def test_delegating_deeper_than_the_bound_is_refused_naming_the_run_that_tried() -> None:
    @workflow
    async def recursing(ctx: WorkflowCtx, depth: int) -> int:
        return int(await ctx.invoke(recursing, depth + 1))

    async with Deck(workflows=[recursing]) as deck:
        with pytest.raises(ConfigError, match=r"'recursing' cannot delegate to 'recursing': that is 4 levels"):
            await deck.run("recursing", 0)


async def test_starting_more_children_than_the_bound_is_refused() -> None:
    @workflow
    async def fanning(ctx: WorkflowCtx) -> list[Any]:
        return await ctx.parallel(*[ctx.invoke(shout, str(index)) for index in range(9)])

    async with Deck(workflows=[shout, fanning]) as deck:
        with pytest.raises(ConfigError, match=r"'fanning' cannot delegate to 'shout': it has already started 8"):
            await deck.run("fanning", None)


@tool
async def shout(word: str) -> str:
    """Say it louder."""
    return word.upper()


# --- ctx.agent -----------------------------------------------------------------------------


async def test_ctx_agent_is_the_agent_whose_turn_is_running() -> None:
    seen: dict[str, Any] = {}

    @tool
    async def whoami(ctx: ToolCtx[Any]) -> str:
        """Report the agent."""
        seen["agent"] = ctx.agent
        return "reported"

    with patch_model(ScriptedModel(deltas=("done",), tool_name="whoami")):
        async with Deck(agents=[Agent(name="Writer", instructions="Draft it.", tools=[whoami])]) as deck:
            await deck.run("Writer", "go")

    assert seen["agent"].name == "Writer"
    assert seen["agent"].declaration.instructions == "Draft it."


async def test_ctx_agent_is_none_in_a_workflow_because_a_workflow_is_not_an_agent() -> None:
    seen: dict[str, Any] = {}

    @workflow
    async def coordinating(ctx: WorkflowCtx) -> str:
        seen["agent"] = ctx.agent
        return "done"

    async with Deck(workflows=[coordinating]) as deck:
        await deck.run("coordinating", None)

    assert seen["agent"] is None


# --- ctx.agents ----------------------------------------------------------------------------


async def test_a_created_agent_runs_as_a_child_and_the_log_records_which() -> None:
    minted: dict[str, Any] = {}

    @workflow
    async def minting(ctx: WorkflowCtx) -> str:
        instance = ctx.agents.create(name="triage", instructions="Rank these.")
        minted["name"] = instance.name
        return str((await ctx.invoke(instance, "three tickets")).output)

    with patch_model(ScriptedModel(deltas=("ranked",))):
        async with Deck(workflows=[minting]) as deck:
            parent = await deck.runs.start("minting", None)
            assert await parent == "ranked"

            assert minted["name"].startswith("triage#")
            child_id = next(run.id for run in await deck.runs.list() if run.id != parent.id)
            assert (await _opening(deck, child_id)).parent_run_id == parent.id
            assert (await _opening(deck, child_id)).invocable == minted["name"]


async def test_two_created_agents_of_one_name_are_two_agents() -> None:
    @workflow
    async def minting(ctx: WorkflowCtx) -> list[str]:
        return [ctx.agents.create(name="triage").name for _ in range(2)]

    async with Deck(workflows=[minting]) as deck:
        first, second = await deck.run("minting", None)
        assert first != second


async def test_a_forked_agent_copies_its_source_and_applies_the_overrides() -> None:
    @workflow
    async def forking(ctx: WorkflowCtx) -> dict[str, Any]:
        instance = ctx.agents.fork("Researcher", instructions="Only peer-reviewed sources.")
        return {"instructions": instance.declaration.instructions, "model": instance.declaration.model}

    async with Deck(agents=[_researcher(model="gpt-4o")], workflows=[forking]) as deck:
        assert await deck.run("forking", None) == {
            "instructions": "Only peer-reviewed sources.",
            "model": "gpt-4o",
        }


async def test_forking_something_this_deck_does_not_hold_is_refused() -> None:
    @workflow
    async def forking(ctx: WorkflowCtx) -> str:
        return ctx.agents.fork("Nobody").name

    async with Deck(agents=[_researcher()], workflows=[forking]) as deck:
        with pytest.raises(NotFoundError, match=r"No agent or workflow named 'Nobody'"):
            await deck.run("forking", None)


async def test_an_agent_instance_this_deck_never_minted_is_refused() -> None:
    @workflow
    async def invoking(ctx: WorkflowCtx) -> str:
        return str(await ctx.invoke(AgentInstance(name="Researcher", declaration=_researcher())))

    async with Deck(agents=[_researcher()], workflows=[invoking]) as deck:
        with pytest.raises(ConfigError, match="does not hold under that name"):
            await deck.run("invoking", None)


async def test_ctx_agents_needs_a_deck_to_mint_into() -> None:
    with pytest.raises(RuntimeError, match="no deck to mint an agent into"):
        _ = WorkflowCtx(None).agents  # ty: ignore[invalid-argument-type]  -  a context built by hand
