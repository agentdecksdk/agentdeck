"""One subject for the shared ``ToolCtx[T]`` contract: a root whose single injected callable is
an agent's tool.

``test_context_injection.py`` holds one test body per property against this subject, which is
the whole reason the subject is built the way it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents import Agent as SDKAgent
from openai_agents_cases import TailScriptedModel

from agentdeck.adapters.executors.openai_agents import OpenAIAgentsExecutor
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


SUBJECTS = [openai_agents_subject]


__all__ = ["ANSWER", "SUBJECTS", "Environment", "Subject", "openai_agents_subject"]
