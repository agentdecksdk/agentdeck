"""What a user callable declares: whether it wants a :class:`~agentdeck.core.context.Context`,
which ``T`` it requires, what is left for the model to fill in, and whether any of that could be
established at all.

Engine-independent on purpose. Both engine bridges compile the same declaration — a plain
callable — into their own native shape, and two copies of "is this parameter a ``Context``" is
how the OpenAI and LangGraph paths would quietly stop agreeing. This module is the one answer
both compile against; it builds nothing and calls nothing.

It lives here rather than in ``core/`` because it raises :class:`ConfigError`, and the error
taxonomy is not core's yet — while every consumer it has (agent compilation, workflow
compilation, and the graph walk behind ``Deck.build()``) is already in ``authoring``.

A parameter is injected because of its annotation, never its name: exactly one ``Context[...]``
parameter is injected whatever it is called, zero means an ordinary callable, and two is a
configuration error rather than a choice AgentDeck makes for the author.

The fence, since this is the seam where it will be tested: permissions, approvals, retries and
telemetry all have a natural home in callable compilation, and none of them are v3.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

from agentdeck.core.context import Context
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

_NOT_A_CONTEXT = object()
"""Distinguishes "this parameter is not a ``Context``" from a genuine ``Context[None]``."""


@dataclass(frozen=True, slots=True)
class CallableAnalysis:
    """What static inspection could establish about one callable.

    ``reliable`` is the field to read first. When it is ``False`` nothing else here is a finding:
    the signature could not be recovered, so no ``Context`` parameter was seen *and none was ruled
    out*, and an unanalyzable callable is indistinguishable from a zero-argument one. Such a
    callable is the invocation-time safety net's problem, not a guess to make at build time.

    ``visible_parameters`` is everything the context parameter is not — what a tool schema is
    built from — with annotations already resolved against the callable's own module, so a
    rebuilt signature cannot pick up a different meaning of the same name.
    """

    target: Callable[..., object]
    context_parameter: str | None
    context_type: object | None
    visible_parameters: tuple[inspect.Parameter, ...]
    reliable: bool


def analyze_callable(target: Callable[..., object]) -> CallableAnalysis:
    """Inspect ``target`` for the one ``Context[...]`` parameter it may declare.

    Raises :class:`ConfigError` naming the callable when it declares more than one.
    """
    # `inspect.signature` follows `__wrapped__` on its own, but `get_type_hints` does not: it
    # would read the annotations `functools.wraps` copied onto the wrapper while resolving them
    # against the *wrapper's* module. Unwrapping first keeps both halves on the same function.
    unwrapped = inspect.unwrap(target)
    try:
        signature = inspect.signature(unwrapped)
        hints = get_type_hints(unwrapped)
    except (NameError, TypeError, ValueError):
        return _unanalyzable(target)

    parameters = list(signature.parameters.values())
    # A decorator that returned a bare `*args, **kwargs` wrapper raises nothing and reports a
    # signature that is simply not the author's. Anything could be hiding behind it, so this
    # reports that it does not know rather than that there is no context parameter.
    if parameters and all(parameter.kind in _VARIADIC for parameter in parameters):
        return _unanalyzable(target)

    declared = [
        (parameter.name, required)
        for parameter in parameters
        if (required := _required_type(hints.get(parameter.name))) is not _NOT_A_CONTEXT
    ]
    if len(declared) > 1:
        names = ", ".join(name for name, _ in declared)
        raise ConfigError(
            f"{_describe(target)} declares multiple Context[...] parameters ({names}); at most one is allowed."
        )
    if not declared:
        return CallableAnalysis(target, None, None, tuple(_resolved(parameters, hints)), reliable=True)

    name, required = declared[0]
    visible = [parameter for parameter in parameters if parameter.name != name]
    return CallableAnalysis(target, name, required, tuple(_resolved(visible, hints)), reliable=True)


def _unanalyzable(target: Callable[..., object]) -> CallableAnalysis:
    return CallableAnalysis(target, None, None, (), reliable=False)


def _required_type(hint: object) -> object:
    """The ``T`` a ``Context[T]`` annotation requires, or :data:`_NOT_A_CONTEXT`.

    A bare ``Context`` reads as ``Context[Any]``. Treating it as an ordinary parameter instead
    would put an AgentDeck internal in a model-visible tool schema, which is the one thing the
    context rule forbids — and the author plainly meant to be injected.
    """
    if hint is Context:
        return Any
    if get_origin(hint) is Context:
        args = get_args(hint)
        return args[0] if args else Any
    return _NOT_A_CONTEXT


def _resolved(parameters: Iterable[inspect.Parameter], hints: Mapping[str, object]) -> Iterator[inspect.Parameter]:
    """Swap each raw annotation for the resolved hint.

    Under ``from __future__ import annotations`` a ``Parameter.annotation`` is the source string,
    and whoever rebuilds a signature from those resolves them against their own globals rather
    than the author's.
    """
    for parameter in parameters:
        if parameter.name in hints:
            yield parameter.replace(annotation=hints[parameter.name])
        else:
            yield parameter


def _describe(target: Callable[..., object]) -> str:
    return getattr(target, "__qualname__", None) or repr(target)


__all__ = ["CallableAnalysis", "analyze_callable"]
