"""``@tool`` and ``@workflow``: the two things AgentDeck can execute that are just Python.

A decorator here classifies a function, checks its context contract, and hands back a definition
the catalog can hold. It creates no run, owns no lifecycle, and never calls the function
(``docs/design/execution-api.md``, Appendix A).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentdeck.authoring.injection import analyze_callable, describe_callable
from agentdeck.core.context import ToolCtx, WorkflowCtx
from agentdeck.core.invocable import InvocableKind
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentdeck.authoring.injection import CallableAnalysis


@dataclass(frozen=True, slots=True)
class NativeDefinition:
    """One AgentDeck-native executable: what it is called, what it needs injected, and the
    function itself.

    The contract check that produces one is the reason there are two decorators rather than one: a
    tool performs a capability and a workflow coordinates executions, so a tool that could suspend
    a person for an answer is no longer a leaf. Checked at import, not at the call that needed it.

    A definition, never a run. The same one serves every invocation, so nothing here is per-call
    and nothing here is mutable.
    """

    name: str
    description: str
    kind: InvocableKind
    call: Callable[..., Any]
    analysis: CallableAnalysis

    @property
    def context_parameter(self) -> str | None:
        """The parameter the runtime injects a context into, if the body asked for one."""
        return self.analysis.context_parameter

    @property
    def context_class(self) -> type | None:
        """Which context it asked for: :class:`ToolCtx` or :class:`WorkflowCtx`."""
        return self.analysis.context_class

    @property
    def parameters(self) -> tuple[str, ...]:
        """The body's own parameters, in order, context excluded."""
        return tuple(parameter.name for parameter in self.analysis.visible_parameters)


def tool(target: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None) -> Any:
    """Declare a leaf capability. The body may take a ``ToolCtx[T]`` and nothing wider.

        @tool
        async def search(ctx: ToolCtx[Corpus], query: str) -> list[str]:
            await ctx.reporter.info("Searching", query=query)
            return await ctx.data.search(query)

    The context parameter is injected by the runtime and is absent from the schema the model is
    shown, which is the reason a tool declaring one must not be pre-decorated with the Agents
    SDK's own ``@function_tool``.
    """
    return _define(InvocableKind.TOOL, ToolCtx, target, name, description)


def workflow(
    target: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None
) -> Any:
    """Declare orchestration: ordinary Python that coordinates other executions.

        @workflow
        async def research(ctx: WorkflowCtx, topic: str) -> Report:
            approved = await ctx.ask(f"Research {topic}?", options=[True, False])
            ...

    The body runs as a coroutine on the native executor. It suspends where it stands  -  an
    ``ask``, an ``approve``, an operator's pause at a ``safepoint``  -  and continues on the next
    line rather than replaying, which is what makes it ordinary Python and not a graph.
    """
    return _define(InvocableKind.WORKFLOW, WorkflowCtx, target, name, description)


def _define(
    kind: InvocableKind,
    required: type,
    target: Callable[..., Any] | None,
    name: str | None,
    description: str | None,
) -> Any:
    """Both decorators, in both spellings: bare, or called with keywords."""
    if target is None:
        return lambda fn: _build(kind, required, fn, name, description)
    return _build(kind, required, target, name, description)


def _build(
    kind: InvocableKind,
    required: type,
    target: Callable[..., Any],
    name: str | None,
    description: str | None,
) -> NativeDefinition:
    if not inspect.iscoroutinefunction(inspect.unwrap(target)):
        raise ConfigError(
            f"{describe_callable(target)} is declared @{kind.value} but is not async. A native "
            f"{kind.value} is awaited by the runtime, and a blocking body would stall every run "
            f"sharing its event loop  -  make it `async def`, and use asyncio.to_thread for work "
            f"that genuinely blocks."
        )
    analysis = analyze_callable(target)
    if not analysis.reliable:
        raise ConfigError(
            f"{describe_callable(target)} cannot be declared @{kind.value}: its signature could not "
            f"be read, so neither its inputs nor its context parameter can be established. A "
            f"decorator that does not use functools.wraps is the usual cause."
        )
    _check_context(kind, required, analysis, target)
    return NativeDefinition(
        name=name or getattr(target, "__name__", None) or describe_callable(target),
        description=description or inspect.getdoc(target) or "",
        kind=kind,
        call=target,
        analysis=analysis,
    )


def _check_context(kind: InvocableKind, required: type, analysis: CallableAnalysis, target: Callable[..., Any]) -> None:
    """Refuse a body asking for a context its kind does not get.

    A tool declaring ``WorkflowCtx`` is the case this exists for: it would silently acquire the
    ability to suspend a person for an answer and to start other runs, which is what separates
    orchestration from a capability. The reverse is refused too, and for a plainer reason  -  a
    workflow declaring ``ToolCtx`` has asked for a surface with no ``invoke`` on it, and would
    fail on its first line.
    """
    declared = analysis.context_class
    if declared is None or declared is required:
        return
    raise ConfigError(
        f"{describe_callable(target)} is declared @{kind.value} but its context parameter "
        f"{analysis.context_parameter!r} asks for {declared.__name__}; a {kind.value} receives "
        f"{required.__name__}. A tool performs a capability and a workflow coordinates executions, "
        f"so the two contexts are not interchangeable  -  see docs/design/execution-api.md."
    )


__all__ = ["NativeDefinition", "tool", "workflow"]
