"""Compile a user's instructions callable into the SDK's dynamic-instructions shape.

The third injection site, through the same analysis as :mod:`agentdeck.authoring.tools` rather
than a mechanism of its own: an instructions callable declaring ``Context[...]`` is compiled
into the ``(wrapper, agent) -> str`` the SDK calls, and the wrapper is unwrapped back to the
portable view before the author's function ever sees it.

The rule this site exists to keep, and the reason it is not simply "return a string": **only
the return value reaches the prompt.** ``ctx.data`` is never projected into instructions by
AgentDeck — a callable that wants the model to know something about its environment has to say
so in the string it returns.

An instructions callable takes its context and nothing else. There is no model-supplied
argument here to leave room for, so a leftover parameter is a mistake with no plausible reading
— nothing would ever fill it — and it is refused rather than guessed at.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from agentdeck.authoring.injection import analyze_callable, check_context_type, describe_callable
from agentdeck.core.context import Context, RunContext
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable


def compile_instructions(
    target: Callable[..., Any], *, context_type: object | None = None
) -> Callable[[Any, Any], Any]:
    """Build the SDK dynamic-instructions callable for ``target``.

    Refused rather than compiled when the signature could not be read: the same reasoning as a
    tool's, since "no ``Context`` parameter was found" is not a finding about an unreadable
    callable — it is the absence of one, and the argument would go missing at the first turn.
    """
    analysis = analyze_callable(target)
    named = describe_callable(target)
    if not analysis.reliable:
        raise ConfigError(
            f"{named} cannot be compiled as instructions: its signature could not be read, so "
            "whether it declares a Context[...] parameter cannot be established. A decorator that "
            "does not use functools.wraps is the usual cause."
        )
    if analysis.visible_parameters:
        extra = ", ".join(parameter.name for parameter in analysis.visible_parameters)
        raise ConfigError(
            f"{named} is used as instructions but declares parameter(s) nothing supplies ({extra}); "
            "an instructions callable takes at most one Context[...] parameter and nothing else."
        )
    check_context_type(analysis, context_type)
    context_parameter = analysis.context_parameter

    # Exactly two parameters, because that is what the SDK's own signature check demands before
    # it will call this at all; both are ignored unless the author asked for the context.
    async def instructions(wrapper: Any, agent: Any) -> Any:
        arguments: dict[str, Any] = {}
        if context_parameter is not None:
            run = wrapper.context
            if not isinstance(run, RunContext):
                raise ConfigError(
                    f"{named} declares a Context[...] parameter, but this run carries "
                    f"{type(run).__name__} rather than an AgentDeck run context — instructions "
                    "compiled by AgentDeck have to be played by an AgentDeck run."
                )
            arguments[context_parameter] = Context(run)
        # A sync body runs inline rather than on a thread, which is what the SDK does with a
        # sync instructions callable of its own: this is a prompt string, not a tool call.
        result = target(**arguments)
        return await result if inspect.isawaitable(result) else result

    instructions.__name__ = getattr(target, "__name__", "instructions")
    instructions.__qualname__ = named
    return instructions


__all__ = ["compile_instructions"]
