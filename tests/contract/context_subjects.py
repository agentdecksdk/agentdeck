"""One subject per engine for the shared ``ToolCtx[T]`` contract: a root whose single injected
callable is the same function written twice, once as an agent's tool and once as a workflow node.

Everything that differs between the two engines is confined here  -  the SDK ``Agent`` and its
scripted model on one side, the ``StateGraph`` and its bridged node on the other  -  so
``test_context_injection.py`` can hold one test body per property and never branch on which
engine it is running against. A property that only one bridge satisfies fails there for that
engine, which is the whole reason the subjects are built to be interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents import Agent as SDKAgent
from langgraph.graph import END, START, StateGraph
from openai_agents_cases import TailScriptedModel
from pydantic import BaseModel

from agentdeck.adapters.executors.langgraph import LangGraphExecutor
from agentdeck.adapters.executors.openai_agents import OpenAIAgentsExecutor
from agentdeck.authoring.graphs import bridge_context_nodes
from agentdeck.authoring.tools import compile_tool
from agentdeck.core.content import TextBlock
from agentdeck.core.context import ToolCtx  # noqa: TC001  -  the subjects below must resolve it at runtime
from agentdeck.core.invocable import InvocableKind, InvocableSpec

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentdeck.core.content import Input
    from agentdeck.core.ports import Executor

ANSWER = "looked"
"""What both subjects report through ``ctx.reporter``, so one assertion covers both."""


class Environment:
    """The application object a run is handed: a live thing, never serialized.

    ``secret`` exists so "the context never reaches the log" can be asserted by searching the
    serialized stream for a string that could only have come from here.
    """

    def __init__(self, secret: str = "the-secret-slot") -> None:
        self.secret = secret


class _State(BaseModel):
    """The workflow's own mutable data  -  the other half of the state/context separation."""

    input: str = ""
    out: str = ""


@dataclass
class Subject:
    """One engine's version of the same root, plus what its injected callable saw."""

    id: str
    executor: Executor
    spec: InvocableSpec
    seen: list[ToolCtx[Environment]] = field(default_factory=list)
    input: Input = field(default_factory=lambda: [TextBlock(text="any slot tuesday?")])


def _peek(seen: list[ToolCtx[Environment]]) -> Callable[..., Any]:
    """The one callable both subjects run. Written once so a difference in what it does cannot
    be mistaken for a difference in what the bridges deliver."""

    async def peek(environment: ToolCtx[Environment]) -> str:
        """Look at the run's environment."""
        seen.append(environment)
        await environment.safepoint()
        await environment.reporter.info(ANSWER)
        # A constant, never anything read off the environment: what a tool returns is recorded,
        # and a subject that echoed its secret would defeat the "never in the log" assertions.
        return "ok"

    return peek


def openai_agents_subject() -> Subject:
    seen: list[ToolCtx[Environment]] = []
    agent = SDKAgent(
        name="Looker",
        instructions="use the tool",
        tools=[compile_tool(_peek(seen))],
        model=TailScriptedModel("done", tool_name="peek"),
    )
    return Subject(
        id="openai-agents",
        executor=OpenAIAgentsExecutor(),
        spec=InvocableSpec(name="Looker", kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=agent),
        seen=seen,
    )


def langgraph_subject() -> Subject:
    seen: list[ToolCtx[Environment]] = []
    peek = _peek(seen)

    async def look(state: _State, environment: ToolCtx[Environment]) -> dict[str, Any]:
        # The node's own parameter list is what the contract is about: ``state`` is the
        # workflow's mutable data and ``environment`` is the application's, side by side and
        # neither derived from the other.
        return {"out": await peek(environment), "input": state.input}

    graph: StateGraph[Any] = StateGraph(_State)
    graph.add_node("look", look)
    graph.add_edge(START, "look")
    graph.add_edge("look", END)
    return Subject(
        id="langgraph",
        executor=LangGraphExecutor(),
        spec=InvocableSpec(
            name="Looker",
            kind=InvocableKind.WORKFLOW,
            executor=LangGraphExecutor.name,
            native=bridge_context_nodes(graph),
        ),
        seen=seen,
    )


SUBJECTS = [openai_agents_subject, langgraph_subject]


__all__ = ["ANSWER", "SUBJECTS", "Environment", "Subject", "langgraph_subject", "openai_agents_subject"]
