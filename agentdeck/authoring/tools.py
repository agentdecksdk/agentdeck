"""Compile a plain user callable into the Agents SDK tool object an agent actually runs.

The bridge half of :mod:`agentdeck.authoring.injection`: that module answers what a callable
declares, this one turns the answer into a ``FunctionTool``. A callable annotated
``ToolCtx[T]`` cannot be pre-decorated with ``@function_tool``  -  the decorator would put the
context parameter in the model-visible schema  -  so compiling it here is the only way the
annotation can mean anything at all. Only a callable that arrived through ``@tool`` may carry
one, though (``declared_via_tool=True``, set by :func:`~agentdeck.authoring.compile.compile_agent`
for a :class:`~agentdeck.authoring.native.NativeDefinition`)  -  a bare function in ``tools=``
declaring ``ToolCtx[...]`` is refused, naming the decorator to add.

What the SDK still owns, and is deliberately not reimplemented: schema generation, JSON
parsing, dispatch, and excluding its own ``RunContextWrapper`` parameter from the schema. The
bridge is a function whose *declared* signature is that wrapper followed by the author's
model-visible parameters, so the SDK builds a schema for exactly the parameters the model may
fill in  -  the context parameter is absent from it because it was never in the signature the SDK
was shown.

The fence, since this is where it will be tested: permissions, approvals, retries and telemetry
all have a natural home in callable compilation, and none of them are v3.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, get_type_hints

from agents import RunContextWrapper, default_tool_error_function, function_tool

from agentdeck.authoring.injection import analyze_callable, check_context_type, describe_callable
from agentdeck.core.context import RunContext, ToolCtx
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from agents.tool import FunctionTool

    from agentdeck.authoring.injection import CallableAnalysis
    from agentdeck.core.workers import SyncToolWorkers


def compile_tool(
    target: Callable[..., Any],
    *,
    context_type: object | None = None,
    declared_via_tool: bool = False,
    workers: SyncToolWorkers | None = None,
) -> FunctionTool:
    """Build the SDK tool for ``target``, injecting its ``ToolCtx[...]`` parameter if it has one.

    A callable whose signature could not be recovered is refused rather than compiled. The
    schema the model is shown has to exist at build time, so an unreadable signature has no
    honest one to offer  -  and "no ``ToolCtx`` parameter was found" is not a finding about such a
    callable, it is the absence of one. Guessing there is nothing to inject would drop the
    argument at the first call, silently.

    ``declared_via_tool`` is set only for a callable that reached here through ``@tool``: a bare
    callable declaring ``ToolCtx[...]`` is refused, since the annotation alone gives it no
    contract and no visible declaration site.

    ``context_type`` is the owning deck's ``Deck(context=...)`` declaration, or ``None`` when it
    made none; an incompatible requirement raises :class:`ContextTypeError` here rather than
    reaching a run that could only fail on the first call.

    ``workers`` is the deck's shared :class:`~agentdeck.core.workers.SyncToolWorkers`, which a
    sync body runs on instead of the interpreter-global default executor; ``None`` for a
    standalone compile with no deck lifecycle to own one, which falls back to
    ``asyncio.to_thread()`` exactly as before.
    """
    analysis = analyze_callable(target)
    if not analysis.reliable:
        raise ConfigError(
            f"{describe_callable(target)} cannot be compiled as a tool: its signature could not be read, "
            "so neither the schema shown to the model nor the presence of a ToolCtx[...] parameter "
            "can be established. A decorator that does not use functools.wraps is the usual cause  -  "
            "fix the decorator, or pass a pre-built Agents SDK tool object instead (engine-native: "
            "it gets no portability guarantee)."
        )
    if analysis.context_parameter is not None and not declared_via_tool:
        raise ConfigError(_undeclared_context_message(target, analysis))
    check_context_type(analysis, context_type)
    # `failure_error_function=_tool_failure` is passed on both branches so a raised tool still
    # lands on `tool.call.completed.error`, whether or not it declares `ToolCtx[...]`. Any other
    # `@function_tool` kwarg (`name_override`, `is_enabled`, ...) belongs right here too, once
    # something needs to forward it; nothing about this split is what stops that today.
    if analysis.context_parameter is None:
        return function_tool(target, failure_error_function=_tool_failure)
    return function_tool(_bridge(analysis, workers), failure_error_function=_tool_failure)


def _undeclared_context_message(target: Callable[..., Any], analysis: CallableAnalysis) -> str:
    """Names the fix in the shape the author can paste: the real function name, its own visible
    parameters, and the context annotation it already wrote  -  reassembled as the ``@tool``
    version of the same signature."""
    ctx_name = (analysis.context_class or ToolCtx).__name__
    hints = get_type_hints(inspect.unwrap(target))
    visible = ", ".join(f"{p.name}: {_type_name(p.annotation)}" for p in analysis.visible_parameters)
    params = f"{visible}, " if visible else ""
    context_arg = f"{analysis.context_parameter}: {ctx_name}[{_type_name(analysis.context_type)}]"
    returns = f" -> {_type_name(hints['return'])}" if "return" in hints else ""
    name = getattr(target, "__name__", None) or describe_callable(target)
    return (
        f"{describe_callable(target)} takes a {ctx_name} parameter ({analysis.context_parameter!r}) but is not "
        f"declared @tool. Only @tool carries a context into a tool  -  add the decorator:\n\n"
        f"    from agentdeck import tool\n\n"
        f"    @tool\n"
        f"    def {name}({params}{context_arg}){returns}: ...\n\n"
        f"(instructions= and hooks= callables take a {ctx_name} without one.)"
    )


def _type_name(annotation: object) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def _tool_failure(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """The SDK's own default error text, plus a side record of the exception for the openai-agents
    engine's translator to read back onto ``tool.call.completed.error`` (#250).

    Delegating to :func:`default_tool_error_function` rather than inlining its string keeps the
    model-visible message byte-identical to what an uncompiled ``@function_tool`` produces today  -
    including its JSON-decode special case  -  for as long as the SDK's own default does.
    """
    run = ctx.context
    call_id = getattr(ctx, "tool_call_id", None)
    if isinstance(run, RunContext) and call_id is not None:
        run.tool_failures[call_id] = f"{type(error).__name__}: {error}"
    return default_tool_error_function(ctx, error)


def _bridge(analysis: CallableAnalysis, workers: SyncToolWorkers | None) -> Callable[..., Any]:
    """A function the SDK sees as ``(wrapper, *visible parameters)`` and that calls the original.

    The declared signature is synthesized rather than written, because the visible parameters are
    the author's own  -  the SDK reads them off ``__signature__``/``__annotations__`` exactly as it
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
                f"{describe_callable(target)} declares a ToolCtx[...] parameter, but this run carries "
                f"{type(run).__name__} rather than an AgentDeck run context  -  a tool compiled by "
                "AgentDeck has to be played by an AgentDeck run."
            )
        # Absent for an awaited body: it never leaves this loop, so it never needs its own facade
        # reporter or a refusal to guard an API it can reach the ordinary way.
        tool_ctx = ToolCtx(run, _loop=None if awaits else asyncio.get_running_loop())
        bound = visible_signature.bind(*supplied, **kwargs)
        # `BoundArguments` already knows how to split a name -> value mapping back into positional
        # and keyword arguments for a signature, including positional-only parameters (which cannot
        # be passed by name) and the *args/**kwargs `bind` above just re-packed.
        call = original.bind_partial()
        call.arguments.update(
            {
                parameter.name: (tool_ctx if parameter.name == context_parameter else bound.arguments[name])
                for parameter in original.parameters.values()
                if (name := parameter.name) == context_parameter or name in bound.arguments
            }
        )
        if awaits:
            return await target(*call.args, **call.kwargs)
        # Parity with what the SDK does for a sync `@function_tool`: a blocking tool body must not
        # run on the event loop, where it would stall the stream and every safe point with it.
        submit = workers.submit if workers is not None else asyncio.to_thread
        result = await submit(target, *call.args, **call.kwargs)
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
    """A name for the wrapper parameter that no visible parameter already has  -  two parameters
    of one name is a ``ValueError`` from ``inspect.Signature``, not a shadowed argument."""
    name = "run_context_wrapper"
    while name in taken:
        name += "_"
    return name


__all__ = ["compile_tool"]
