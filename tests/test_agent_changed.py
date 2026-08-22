"""``agent.changed`` (#249): the OpenAI Agents mapping, and the two things that must never
produce it  -  a handoff that was requested but never completed, and a run started from inside
another run.

``tests/test_handoff_round_trip.py`` already covers the success path end to end (``Deck``, a
real handoff cycle). This file covers ``translate()``'s own boundary, in the same offline,
no-live-model style as ``tests/test_openai_agents_tool_failure.py`` (``types.SimpleNamespace``,
hand-built SDK objects, a scripted ``Model``), plus the two negative cases the issue names.

``ctx.invoke()`` and child runs do not exist on this tree yet at all: ``core/context.py`` says
they "join in the PR that adds child runs". The closest thing that exists today is a tool that
itself starts a second run through the public ``Deck`` API  -  a genuinely different run, with
its own ``run.started``, which is exactly the boundary the issue draws the line at.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
from agents import Agent
from agents.handoffs import handoff

from agentdeck.adapters.executors.openai_agents import ExecutionStore, OpenAIAgentsExecutor
from agentdeck.adapters.executors.openai_agents.translate import translate
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.authoring import Agent as DeckAgent
from agentdeck.core.content import TextBlock
from agentdeck.core.events import AgentChanged, RunFailed
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.deck import Deck
from agentdeck.runtime.service import Runtime
from agentdeck.testing import ScriptedModel, patch_model

# --- translate()'s own boundary: success maps, a bare request does not ---------------------


def _handoff_output_item(source: str, target: str) -> Any:
    item = types.SimpleNamespace(
        type="handoff_output_item",
        source_agent=types.SimpleNamespace(name=source),
        target_agent=types.SimpleNamespace(name=target),
    )
    return types.SimpleNamespace(type="run_item_stream_event", item=item)


def _handoff_call_item() -> Any:
    item = types.SimpleNamespace(type="handoff_call_item")
    return types.SimpleNamespace(type="run_item_stream_event", item=item)


def test_a_completed_handoff_translates_to_agent_changed() -> None:
    payload = translate(_handoff_output_item("FrontDesk", "ClaimsAgent"), {}, {})

    assert isinstance(payload, AgentChanged)
    assert (payload.previous_agent, payload.next_agent) == ("FrontDesk", "ClaimsAgent")


def test_a_handoff_request_with_no_completed_output_emits_nothing() -> None:
    """``handoff_call_item`` is the request half, paired with ``handoff_output_item``. On its
    own  -  which is what a request the SDK never finishes looks like at this boundary  -  it
    maps to nothing, same as every other item type this translator does not surface."""
    assert translate(_handoff_call_item(), {}, {}) is None


# --- a refused handoff fails the run, and produces no agent.changed either ------------------


def _refuse(_ctx: Any) -> None:
    raise RuntimeError("handoff refused")


async def test_a_refused_handoff_fails_the_run_with_no_agent_changed_in_the_log() -> None:
    """Empirically verified against the Agents SDK: an ``on_handoff`` callback that raises
    propagates out of the transfer tool call, and the run never produces the
    ``handoff_output_item`` the translator would need to see  -  so alongside the run's own
    failure, the log carries no ``agent.changed`` for the handoff that was requested."""
    claims_agent = Agent(name="ClaimsAgent", instructions="handle claims")
    declined = handoff(claims_agent, on_handoff=_refuse)
    front_agent = Agent(
        name="FrontDesk", instructions="route", handoffs=[declined], model=ScriptedModel(tool_name=declined.tool_name)
    )
    spec = InvocableSpec(
        name="FrontDesk", kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=front_agent
    )
    runtime = Runtime([OpenAIAgentsExecutor(ExecutionStore())], SqliteEventStore(), {"FrontDesk": spec})

    events = []
    with pytest.raises(RuntimeError, match="handoff refused"):
        async for event in runtime.run("FrontDesk", [TextBlock(text="hi")], session_id="s1"):
            events.append(event)

    assert not any(isinstance(event.payload, AgentChanged) for event in events)
    failed = next(event.payload for event in events if isinstance(event.payload, RunFailed))
    assert failed.error_code == "engine_error"


# --- a run started from inside another run is a different run, not a changed agent ---------


async def test_a_tool_started_run_is_a_different_run_with_no_agent_changed_in_either_log() -> None:
    deck_box: list[Deck] = []
    child_runs: list[Any] = []

    async def spawn_child() -> str:
        """Start a second run and wait for it, the way a tool with no formal invocation API
        still can today."""
        child = await deck_box[0].runs.start("Helper", "handle it", session_id="child-1")
        await child
        child_runs.append(child)
        return "spawned"

    frontline = DeckAgent(name="Frontline", instructions="use the tool", tools=[spawn_child])
    helper = DeckAgent(name="Helper", instructions="answer plainly")
    deck = Deck(agents=[frontline, helper])
    deck_box.append(deck)
    deck.build()

    with patch_model(ScriptedModel(deltas=("done",), tool_name="spawn_child")):
        async with deck:
            events = [event async for event in deck.stream("Frontline", "please help", session_id="parent-1")]
            child_events = [event async for event in child_runs[0].events()]

    assert child_runs[0].id != events[0].run_id  # a distinct run, not a continuation
    assert not any(isinstance(event.payload, AgentChanged) for event in events)
    assert not any(isinstance(event.payload, AgentChanged) for event in child_events)
    assert child_events[0].kind == "run.started"  # "something else ran" is read here, not off a changed agent
