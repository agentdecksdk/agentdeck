"""Compile a plain user callable into the Agents SDK tool object an agent actually runs.

The bridge half of :mod:`agentdeck.authoring.injection`: that module answers what a callable
declares, this one turns the answer into a ``FunctionTool``. A callable annotated
``Context[T]`` cannot be pre-decorated with ``@function_tool`` — the decorator would put the
context parameter in the model-visible schema — so compiling it here is the only way the
annotation can mean anything at all.

What the SDK still owns, and is deliberately not reimplemented: schema generation, JSON
parsing, dispatch, and excluding its own ``RunContextWrapper`` parameter from the schema. The
bridge is a function whose *declared* signature is that wrapper followed by the author's
model-visible parameters, so the SDK builds a schema for exactly the parameters the model may
fill in — the context parameter is absent from it because it was never in the signature the SDK
was shown.

The fence, since this is where it will be tested: permissions, approvals, retries and telemetry
all have a natural home in callable compilation, and none of them are v3.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from agents import RunContextWrapper, function_tool

from agentdeck.authoring.injection import analyze_callable, describe_callable
from agentdeck.core.context import Context, RunContext
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from agents.tool import FunctionTool

    from agentdeck.authoring.injection import CallableAnalysis


def compile_tool(target: Callable[..., Any]) -> FunctionTool:
    """Build the SDK tool for ``target``, injecting its ``Context[...]`` parameter if it has one.

    A callable whose signature could not be recovered is refused rather than compiled. The
    schema the model is shown has to exist at build time, so an unreadable signature has no
    honest one to offer — and "no ``Context`` parameter was found" is not a finding about such a
    callable, it is the absence of one. Guessing there is nothing to inject would drop the
    argument at the first call, silently.
    """
    analysis = analyze_callable(target)
    if not analysis.reliable:
        raise ConfigError(
            f"{describe_callable(target)} cannot be compiled as a tool: its signature could not be read, "
            "so neither the schema shown to the model nor the presence of a Context[...] parameter "
            "can be established. A decorator that does not use functools.wraps is the usual cause — "
            "fix the decorator, or pass a pre-built Agents SDK tool object instead (engine-native: "
            "it gets no portability guarantee)."
        )
    if analysis.context_parameter is None:
        return function_tool(target)
    return function_tool(_bridge(analysis))


def _bridge(analysis: CallableAnalysis) -> Callable[..., Any]:
    """A function the SDK sees as ``(wrapper, *visible parameters)`` and that calls the original.

    The declared signature is synthesized rather than written, because the visible parameters are
    the author's own — the SDK reads them off ``__signature__``/``__annotations__`` exactly as it
    would from a hand-written ``def``, and hands back the arguments the model supplied.
    """
    # The analysis types its subject's return as `object`, which it cannot know anything about;
    # here it is whatever the tool returns, and the async branch below awaits it.
    target: Callable[..., Any] = analysis.target
    original = inspect.signature(inspect.unwrap(target))
    visible = analysis.visible_parameters
    visible_signature = inspect.Signature(list(visible))
    context_parameter = analysis.context_parameter
    # `functools.wraps` copies neither, and `inspect.iscoroutinefunction` follows neither
    # `__wrapped__` nor the reverse case, so both the wrapper and what it wraps get a look:
    # a sync wrapper around an async function is what `functools.wraps` most often produces.
    awaits = inspect.iscoroutinefunction(target) or inspect.iscoroutinefunction(inspect.unwrap(target))
    # Positional-only so the parameters that follow it keep whatever kinds the author gave them:
    # a positional-only visible parameter after a positional-or-keyword wrapper is not a valid
    # signature. The SDK passes its context positionally regardless.
    wrapper_name = _unused_name({parameter.name for parameter in visible})
    wrapper_parameter = inspect.Parameter(wrapper_name, inspect.Parameter.POSITIONAL_ONLY)

    async def bridge(*args: Any, **kwargs: Any) -> Any:
        wrapper, *supplied = args
        run = wrapper.context
        if not isinstance(run, RunContext):
            # The invocation-time safety net: this tool was compiled for an AgentDeck run and is
            # being played by something else, so there is no context to inject and calling the
            # original with one argument missing would fail somewhere less legible than here.
            raise ConfigError(
                f"{describe_callable(target)} declares a Context[...] parameter, but this run carries "
                f"{type(run).__name__} rather than an AgentDeck run context — a tool compiled by "
                "AgentDeck has to be played by an AgentDeck run."
            )
        bound = visible_signature.bind(*supplied, **kwargs)
        # `BoundArguments` already knows how to split a name -> value mapping back into positional
        # and keyword arguments for a signature, including positional-only parameters (which cannot
        # be passed by name) and the *args/**kwargs `bind` above just re-packed.
        call = original.bind_partial()
        call.arguments.update(
            {
                parameter.name: (Context(run) if parameter.name == context_parameter else bound.arguments[name])
                for parameter in original.parameters.values()
                if (name := parameter.name) == context_parameter or name in bound.arguments
            }
        )
        if awaits:
            return await target(*call.args, **call.kwargs)
        # Parity with what the SDK does for a sync `@function_tool`: a blocking tool body must not
        # run on the event loop, where it would stall the stream and every safe point with it.
        result = await asyncio.to_thread(target, *call.args, **call.kwargs)
        return await result if inspect.isawaitable(result) else result

    bridge.__name__ = getattr(target, "__name__", "tool")
    bridge.__qualname__ = describe_callable(target)
    bridge.__doc__ = target.__doc__
    bridge.__signature__ = inspect.Signature([wrapper_parameter, *visible])  # ty: ignore[unresolved-attribute]
    bridge.__annotations__ = {
        wrapper_name: RunContextWrapper[RunContext],
        **{
            parameter.name: parameter.annotation
            for parameter in visible
            if parameter.annotation is not inspect.Parameter.empty
        },
    }
    return bridge


def _unused_name(taken: set[str]) -> str:
    """A name for the wrapper parameter that no visible parameter already has — two parameters
    of one name is a ``ValueError`` from ``inspect.Signature``, not a shadowed argument."""
    name = "run_context_wrapper"
    while name in taken:
        name += "_"
    return name


__all__ = ["compile_tool"]
