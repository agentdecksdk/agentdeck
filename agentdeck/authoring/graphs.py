"""Compile the nodes of a user's ``StateGraph`` into nodes langgraph can call with a
:class:`~agentdeck.core.context.Context`.

The langgraph half of :mod:`agentdeck.authoring.injection`, and the sibling of
:mod:`agentdeck.authoring.tools`: the same analysis, a different engine-native shape. A tool
becomes a ``FunctionTool`` whose declared signature starts with the SDK's own wrapper; a node
becomes a callable whose declared signature carries langgraph's ``runtime`` parameter, which is
the channel langgraph fills from the ``context=`` the engine passes to ``astream``.

Why a rewrite rather than a declaration: langgraph injects **by parameter name** — ``runtime``,
``config``, ``writer``, ``store`` — so a parameter annotated ``Context[...]`` under any name
reaches a node only if something puts it there. Nothing else can: the author builds the
``StateGraph`` themselves, so a node's one compilation seam is the graph AgentDeck is handed.

Only AgentDeck-managed nodes are touched — a plain callable passed to ``add_node``. A node that
is any other kind of ``Runnable`` (a chain, a nested compiled graph) is engine-native and left
exactly as it is, and so is a callable whose signature could not be read: unlike a tool, a node
has no model-visible schema that must exist at build time, so leaving it alone changes nothing
about how it runs today.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from typing import TYPE_CHECKING, Any

from langgraph.utils.runnable import RunnableCallable

from agentdeck.authoring.injection import analyze_callable, describe_callable
from agentdeck.core.context import Context, RunContext
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from langgraph.graph.state import StateGraph

    from agentdeck.authoring.injection import CallableAnalysis

RUNTIME_PARAMETER = "runtime"
"""The one parameter name langgraph fills with its ``Runtime``, which carries ``context=``.

Name-based, and langgraph's rule rather than a choice here — which is exactly why the public
contract cannot be a parameter name and a node declaring ``Context[...]`` has to be rewritten
into one that declares this.
"""


def bridge_context_nodes(graph: StateGraph[Any]) -> StateGraph[Any]:
    """Rewrite every node of ``graph`` that declares a ``Context[...]`` parameter, in place.

    Returns the same graph, so a caller can wrap ``build_graph()`` in one expression. Raises
    :class:`ConfigError` naming the node when a node's callable declares more than one
    ``Context[...]`` parameter — at ``build()``, the same moment an agent's tool would.
    """
    for name, node in graph.nodes.items():
        # `.func`/`.afunc` are how langgraph's own `coerce_to_runnable` stores the callable it
        # was handed: `func` for a sync one, `afunc` alone for an async one. Reading them back
        # is the coupling this module accepts in exchange for leaving `add_node` untouched.
        runnable = node.runnable
        if not isinstance(runnable, RunnableCallable):
            continue
        target = runnable.func if runnable.func is not None else runnable.afunc
        if target is None:
            continue
        try:
            analysis = analyze_callable(target)
        except ConfigError as refused:
            raise ConfigError(f"node {name!r}: {refused}") from refused
        if not analysis.reliable or analysis.context_parameter is None:
            continue
        graph.nodes[name] = dataclasses.replace(
            node, runnable=RunnableCallable(None, _bridge(analysis), name=name, trace=False)
        )
    return graph


def _bridge(analysis: CallableAnalysis) -> Callable[..., Any]:
    """A node langgraph sees as ``(state, runtime, *whatever else the author declared)``.

    langgraph passes the state positionally and every injected parameter by keyword, so the
    bridge takes exactly that shape and hands the original its own parameters back — including
    a ``config`` or ``writer`` the author also declared, which travel through untouched.
    """
    # The analysis types its subject's return as `object`, which it cannot know anything about;
    # here it is whatever the node returns, and the async branch below awaits it.
    target: Callable[..., Any] = analysis.target
    original = inspect.signature(inspect.unwrap(target))
    visible = analysis.visible_parameters
    context_parameter = analysis.context_parameter
    # Only when the author did not ask for it themselves: two parameters of one name is a
    # `ValueError` from `inspect.Signature`, and a node that declared `runtime` still wants it.
    declared_runtime = any(parameter.name == RUNTIME_PARAMETER for parameter in visible)
    awaits = inspect.iscoroutinefunction(target) or inspect.iscoroutinefunction(inspect.unwrap(target))

    async def bridge(state: Any, **injected: Any) -> Any:
        runtime = injected[RUNTIME_PARAMETER] if declared_runtime else injected.pop(RUNTIME_PARAMETER)
        run = runtime.context
        if not isinstance(run, RunContext):
            # The invocation-time safety net, the same one a compiled tool has: this node was
            # compiled for an AgentDeck run and is being played by something else, so there is
            # no context to inject and calling the original with an argument missing would fail
            # somewhere less legible than here.
            raise ConfigError(
                f"{describe_callable(target)} declares a Context[...] parameter, but this run carries "
                f"{type(run).__name__} rather than an AgentDeck run context — a node compiled by "
                "AgentDeck has to be played by an AgentDeck run."
            )
        supplied = {context_parameter: Context(run), **injected}
        if visible:
            # The state is langgraph's one positional argument, so it is the author's first
            # parameter whatever they named it.
            supplied[visible[0].name] = state
        # `BoundArguments` already knows how to split a name -> value mapping back into
        # positional and keyword arguments, including for positional-only parameters.
        call = original.bind_partial()
        call.arguments.update(
            {name: supplied[name] for parameter in original.parameters.values() if (name := parameter.name) in supplied}
        )
        if awaits:
            return await target(*call.args, **call.kwargs)
        # Parity with what langgraph does for a sync node: a blocking body must not run on the
        # event loop, where it would stall the stream and every safe point with it.
        result = await asyncio.to_thread(target, *call.args, **call.kwargs)
        return await result if inspect.isawaitable(result) else result

    bridge.__name__ = getattr(target, "__name__", "node")
    bridge.__qualname__ = describe_callable(target)
    bridge.__doc__ = target.__doc__
    parameters = [inspect.Parameter("state", inspect.Parameter.POSITIONAL_ONLY), *visible[1:]]
    if not declared_runtime:
        parameters.append(inspect.Parameter(RUNTIME_PARAMETER, inspect.Parameter.KEYWORD_ONLY))
    bridge.__signature__ = inspect.Signature(parameters)  # ty: ignore[unresolved-attribute]
    return bridge


__all__ = ["RUNTIME_PARAMETER", "bridge_context_nodes"]
