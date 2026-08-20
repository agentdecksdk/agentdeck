"""Compiling a plain callable into an SDK tool, and the ``context=`` that reaches it.

The rule these tests exist to defend is the design's strongest one: possessing a ``ToolCtx[T]``
gives *code* access to the run's dependencies and never gives the *model* access to them. So the
central assertion is an absence  -  the context parameter must not appear anywhere in the schema
the SDK builds  -  and a test that only proved the call works would pass while leaking that
parameter into the prompt.

No live model: the SDK boundary is the scripted model, and the tool invocations below drive the
compiled ``FunctionTool`` directly through the SDK's own ``on_invoke_tool``.
"""

from __future__ import annotations

import functools
import json
import threading
from typing import Any

import pytest
from agents import WebSearchTool, function_tool
from agents.tool_context import ToolContext
from langgraph.graph import END, StateGraph
from pydantic import BaseModel

import agentdeck
from agentdeck.authoring import Agent, Workflow
from agentdeck.authoring.tools import compile_tool
from agentdeck.core.context import RunContext, ToolCtx
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError
from agentdeck.testing import ScriptedModel, patch_model


class Calendar:
    """The sort of thing an application hands a run: a live object, never serialized."""

    def __init__(self, slot: str = "09:00") -> None:
        self.slot = slot

    def find(self, day: str) -> str:
        return f"{day} {self.slot}"


async def find_slots(day: str, environment: ToolCtx[Calendar]) -> str:
    """Find free appointment slots on a given day."""
    return environment.data.find(day)


def sync_find_slots(day: str, environment: ToolCtx[Calendar]) -> str:
    """The same tool, written synchronously."""
    return environment.data.find(day)


async def whoami(environment: ToolCtx[Calendar]) -> str:
    """Report which run is asking."""
    return f"{environment.run_id}/{environment.data.slot}"


async def bare(environment: ToolCtx) -> str:
    """A context with no type argument at all."""
    return str(environment.data)


def _invoke(tool: Any, run: RunContext, arguments: str = "{}") -> Any:
    """Call ``tool`` the way the SDK's run loop does  -  through its own dispatch, not around it."""
    context = ToolContext(context=run, tool_name=tool.name, tool_call_id="call_1", tool_arguments=arguments)
    return tool.on_invoke_tool(context, arguments)


# --- the model never sees the context parameter ------------------------------------------------


def test_the_context_parameter_is_absent_from_the_generated_tool_schema() -> None:
    """The whole point. ``environment`` is a parameter of the user's function and must appear
    nowhere in what is sent to the model  -  not as a property, not as a required name, not as a
    title, not anywhere in the serialized schema."""
    tool = compile_tool(find_slots)

    assert sorted(tool.params_json_schema["properties"]) == ["day"]
    assert tool.params_json_schema["required"] == ["day"]
    assert "environment" not in json.dumps(tool.params_json_schema)


def test_the_wrapper_parameter_the_bridge_adds_is_absent_from_the_schema_too() -> None:
    """The bridge introduces a parameter of its own for the SDK's context object. If the SDK
    stopped recognising it, it would land in the schema as a model-fillable argument."""
    tool = compile_tool(find_slots)

    assert "run_context_wrapper" not in json.dumps(tool.params_json_schema)


def test_a_context_only_tool_advertises_no_parameters_at_all() -> None:
    tool = compile_tool(whoami)

    assert tool.params_json_schema["properties"] == {}


def test_the_tool_is_named_and_described_after_the_users_function_not_the_bridge() -> None:
    tool = compile_tool(find_slots)

    assert tool.name == "find_slots"
    assert tool.description == "Find free appointment slots on a given day."


# --- and the code does ---------------------------------------------------------------------------


async def test_the_declared_context_reaches_the_callable_by_reference() -> None:
    """``ctx.data`` is the very object the caller supplied  -  not a copy, not a projection."""
    calendar = Calendar()
    received: list[Any] = []

    async def record(environment: ToolCtx[Calendar]) -> str:
        """Record what it was handed."""
        received.append(environment.data)
        return "ok"

    await _invoke(compile_tool(record), RunContext(run_id="run-1", data=calendar))

    assert received == [calendar]
    assert received[0] is calendar


async def test_the_model_supplied_arguments_and_the_context_arrive_together() -> None:
    result = await _invoke(compile_tool(find_slots), RunContext(run_id="run-1", data=Calendar()), '{"day": "tue"}')

    assert result == "tue 09:00"


async def test_the_run_identity_travels_on_the_context_as_well_as_the_data() -> None:
    run = RunContext(run_id="run-7", session_id="s-1", data=Calendar(slot="11:00"))

    assert await _invoke(compile_tool(whoami), run) == "run-7/11:00"


async def test_a_bare_context_annotation_is_injected_like_any_other() -> None:
    """Confirms slice 1's reading now that the schema builder exists: an unparameterised
    ``ToolCtx`` is injected, so the internal never reaches the schema."""
    tool = compile_tool(bare)

    assert tool.params_json_schema["properties"] == {}
    assert await _invoke(tool, RunContext(run_id="run-1", data="the environment")) == "the environment"


async def test_a_synchronous_tool_body_runs_off_the_event_loop() -> None:
    """Parity with the SDK's own handling of a sync ``@function_tool``: a blocking body must not
    run on the loop, where it would stall the stream and every safe point with it."""
    ran_on: list[threading.Thread] = []

    def blocking(environment: ToolCtx[Calendar]) -> str:
        """Records which thread ran it."""
        ran_on.append(threading.current_thread())
        return environment.data.find("mon")

    result = await _invoke(compile_tool(blocking), RunContext(run_id="run-1", data=Calendar()))

    assert result == "mon 09:00"
    assert ran_on[0] is not threading.current_thread()


async def test_a_sync_tool_declaring_a_context_still_returns_its_value() -> None:
    result = await _invoke(compile_tool(sync_find_slots), RunContext(run_id="r", data=Calendar()), '{"day": "wed"}')

    assert result == "wed 09:00"


async def test_a_wraps_decorated_callable_compiles_to_the_function_it_wraps() -> None:
    """``functools.wraps`` leaves a *sync* wrapper around an async function; dispatching on the
    wrapper alone would hand the SDK an un-awaited coroutine as the tool's result."""

    def logged(fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    @logged
    async def decorated(day: str, environment: ToolCtx[Calendar]) -> str:
        """Find free slots, through a decorator."""
        return environment.data.find(day)

    tool = compile_tool(decorated)

    assert sorted(tool.params_json_schema["properties"]) == ["day"]
    assert await _invoke(tool, RunContext(run_id="r", data=Calendar()), '{"day": "fri"}') == "fri 09:00"


# --- a callable that declares nothing is still compiled -------------------------------------------


async def test_a_callable_with_no_context_is_compiled_as_an_ordinary_tool() -> None:
    def shout(word: str) -> str:
        """Shout a word."""
        return word.upper()

    tool = compile_tool(shout)

    assert sorted(tool.params_json_schema["properties"]) == ["word"]
    context = ToolContext(context=RunContext(run_id="r"), tool_name="shout", tool_call_id="c", tool_arguments="{}")
    assert await tool.on_invoke_tool(context, '{"word": "hi"}') == "HI"


# --- an unreadable signature is refused, never guessed at -----------------------------------------


def test_a_callable_whose_signature_cannot_be_read_is_refused_rather_than_compiled() -> None:
    """The failure this whole slice is shaped against: an unreadable signature is not evidence
    that no context was declared, and compiling it as if it were would drop the argument."""

    def destroying(fn: Any) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    @destroying
    async def obscured(day: str, environment: ToolCtx[Calendar]) -> str:
        return environment.data.find(day)

    with pytest.raises(ConfigError) as raised:
        compile_tool(obscured)

    message = str(raised.value)
    assert "signature could not be read" in message
    assert "functools.wraps" in message


def test_two_context_parameters_are_still_a_configuration_error_at_compile_time() -> None:
    async def greedy(here: ToolCtx[Calendar], also: ToolCtx[Calendar]) -> None: ...

    with pytest.raises(ConfigError, match="at most one"):
        compile_tool(greedy)


async def test_a_tool_played_by_a_foreign_run_says_so_instead_of_failing_obscurely() -> None:
    """The invocation-time safety net. A compiled tool handed some other framework's context has
    nothing to inject; the bridge raises before calling the user's function, rather than letting
    it fail somewhere less legible with one argument missing.

    The raise surfaces as a tool-failure result because that is what the SDK does with *every*
    tool exception  -  the point asserted here is that the body never ran and the reason is named.
    """
    called: list[bool] = []

    async def never(environment: ToolCtx[Calendar]) -> str:
        """Must not run."""
        called.append(True)
        return "ran"

    tool = compile_tool(never)
    context = ToolContext(context="not a run context", tool_name=tool.name, tool_call_id="c", tool_arguments="{}")

    result = await tool.on_invoke_tool(context, "{}")

    assert called == []
    assert "AgentDeck run context" in result


# --- the same thing, end to end through Deck.run(context=) ------------------------------------------


class _CallsTheToolOnce(ScriptedModel):
    """Calls ``tool_name`` on the first turn, then answers with whatever the tool returned."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(deltas=("done",), tool_name=tool_name)


def _tool_agent(tool: Any, name: str = "Booker") -> Agent:
    return Agent(name=name, instructions="Use the tool, then answer.", tools=[tool])


@pytest.fixture
def no_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.mark.asyncio
async def test_deck_run_hands_its_context_to_a_tool_that_declared_one(no_project) -> None:
    """The public surface, end to end: one object into ``run(context=)``, the same object out of
    ``ctx.data`` inside a tool the SDK dispatched."""
    seen: list[Any] = []

    async def peek(environment: ToolCtx[Calendar]) -> str:
        """Look at the environment."""
        seen.append(environment.data)
        return environment.data.find("mon")

    calendar = Calendar(slot="14:00")
    deck = Deck(agents=[_tool_agent(peek)])
    deck.build()

    with patch_model(_CallsTheToolOnce("peek")):
        async with deck:
            await deck.run("Booker", "when am I free?", context=calendar)

    assert seen == [calendar]
    assert seen[0] is calendar


@pytest.mark.asyncio
async def test_deck_stream_carries_the_context_the_same_way(no_project) -> None:
    seen: list[Any] = []

    async def peek(environment: ToolCtx[Calendar]) -> str:
        """Look at the environment."""
        seen.append(environment.data)
        return "ok"

    calendar = Calendar()
    deck = Deck(agents=[_tool_agent(peek)])
    deck.build()

    with patch_model(_CallsTheToolOnce("peek")):
        async with deck:
            [event async for event in deck.stream("Booker", "hi", context=calendar)]

    assert seen[0] is calendar


@pytest.mark.asyncio
async def test_a_run_without_a_context_reaches_a_declaring_tool_with_none(no_project) -> None:
    """``context=`` is optional, and omitting it is not an error  -  the tool simply gets ``None``,
    which is the value the application declined to supply."""
    seen: list[Any] = []

    async def peek(environment: ToolCtx[Calendar | None]) -> str:
        """Look at the environment."""
        seen.append(environment.data)
        return "ok"

    deck = Deck(agents=[_tool_agent(peek)])
    deck.build()

    with patch_model(_CallsTheToolOnce("peek")):
        async with deck:
            await deck.run("Booker", "hi")

    assert seen == [None]


@pytest.mark.asyncio
async def test_the_context_is_never_written_to_the_event_log(no_project) -> None:
    """Lifecycle rule: the log records what a run was asked to do, not the live objects it held."""

    async def peek(environment: ToolCtx[Calendar]) -> str:
        """Look at the environment."""
        return "ok"

    deck = Deck(agents=[_tool_agent(peek)])
    deck.build()

    with patch_model(_CallsTheToolOnce("peek")):
        async with deck:
            events = [event async for event in deck.stream("Booker", "hi", context=Calendar(slot="secret-slot"))]

    dumped = json.dumps([event.model_dump(mode="json") for event in events])
    assert "secret-slot" not in dumped
    assert "Calendar" not in dumped


# --- a workflow run is unaffected by all of this -----------------------------------------------------
# ``context=`` on a workflow used to raise here; it works now, and lives in
# ``tests/test_node_compilation.py`` with the rest of the langgraph bridge.


class _ShoutState(BaseModel):
    input: str = ""
    shouted: str = ""


def _shout_workflow() -> Workflow:
    def build() -> StateGraph:
        graph = StateGraph(_ShoutState)
        graph.add_node("shout", lambda s: {"shouted": s.input.upper()})
        graph.set_entry_point("shout")
        graph.add_edge("shout", END)
        return graph

    return Workflow(name="Shout", state=_ShoutState, graph=build)


@pytest.mark.asyncio
async def test_a_workflow_whose_nodes_declare_nothing_ignores_a_context(no_project, monkeypatch) -> None:
    """Accepted and unread is fine here, exactly as it is for an agent with no declaring tool:
    the node asked for nothing, so there is nothing to hand it."""
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    deck = Deck(workflows=[_shout_workflow()])
    deck.build()

    async with deck:
        assert await deck.run("Shout", {"input": "hi"}, context=Calendar()) == {"input": "hi", "shouted": "HI"}


@pytest.mark.asyncio
async def test_a_workflow_run_without_a_context_is_unaffected(no_project, monkeypatch) -> None:
    monkeypatch.setenv("AGENTDECK_CHECKPOINT", "memory://")
    deck = Deck(workflows=[_shout_workflow()])
    deck.build()

    async with deck:
        assert await deck.run("Shout", {"input": "hi"}) == {"input": "hi", "shouted": "HI"}


# --- the export ---------------------------------------------------------------------------------


def test_context_is_exported_from_the_package_root() -> None:
    """A tool signature names ``agentdeck.ToolCtx``  -  importing it from a private module would
    make the one portable type look like an internal."""
    assert agentdeck.ToolCtx is ToolCtx
    assert "ToolCtx" in agentdeck.__all__


def test_a_pre_built_sdk_tool_object_is_still_passed_straight_through(no_project) -> None:
    """Engine-native, and nothing here introspects it."""

    @function_tool
    def lookup(q: str) -> str:
        """Look something up."""
        return q

    hosted = WebSearchTool()
    deck = Deck(agents=[Agent(name="Native", instructions="x", tools=[lookup, hosted])])
    deck.build()

    compiled = deck._invocables["Native"].native.tools
    assert compiled[0] is lookup
    assert compiled[1] is hosted
