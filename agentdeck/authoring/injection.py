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

The requirement each analysis reports is also what a deck's own ``context=`` declaration is
checked against, which is why :func:`check_context_type` lives beside the analysis rather than
in whichever compiler happens to hold the answer first — four compilers deciding separately
what "compatible" means is the same drift two copies of the analysis would be.

The fence, since this is the seam where it will be tested: permissions, approvals, retries and
telemetry all have a natural home in callable compilation, and none of them are v3.
"""

from __future__ import annotations

import inspect
import types
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin, get_type_hints

from agentdeck.core.context import Context
from agentdeck.errors import ConfigError, ContextTypeError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

_VARIADIC = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)

_UNION_FORMS = frozenset({types.UnionType, Union})
"""Both spellings of a union origin. ``A | B`` and ``Union[A, B]`` are one object from 3.14 on
and two before it, and the older one is *itself a class* — so falling through to ``issubclass``
would compare a context type against ``UnionType`` and refuse every union that is in fact fine.
"""

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
            f"{describe_callable(target)} declares multiple Context[...] parameters ({names}); at most one is allowed."
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


def declared_context_type(value: object) -> object | None:
    """Validate a deck's ``context=`` declaration and hand it back, or refuse it.

    The declaration is a *type*, and the natural mistake is to pass the value instead. Refused
    rather than accepted, because an instance makes every compatibility check below defer: the
    parameter would read as a guarantee and buy nothing, which is the accepted-and-useless shape
    it was deleted for once already. A parameterised generic (``Mapping[str, Any]``) is a type
    this can check against; a union is not, so it is refused here rather than quietly deferred
    at every callable in the catalog.
    """
    if value is None:
        return None
    origin = get_origin(value)
    if origin not in _UNION_FORMS and isinstance(origin or value, type):
        return value
    raise ConfigError(
        f"Deck(context=...) declares the type of the application context this deck's callables "
        f"require, and {value!r} is not one. Pass the class (Deck(context=MiddleContext)); the "
        f"value itself goes to the run (deck.run(..., context=middle_context))."
    )


def check_context_type(analysis: CallableAnalysis, declared: object | None) -> None:
    """Refuse ``analysis``'s ``Context[T]`` requirement when a deck's declared type cannot meet it.

    A no-op unless both halves are present: a deck that declares no ``context=`` keeps every
    requirement unchecked at build time, exactly as it was before the declaration existed, and a
    callable declaring no ``Context[...]`` has nothing to check.

    Deliberately not a type checker. Only what the runtime can actually answer is answered —
    an exact type, a subtype, ``Any``, a runtime ABC, a protocol ``issubclass`` will decide on.
    Everything else defers, and stands or falls at invocation instead of on a guess here.
    """
    if declared is None or analysis.context_parameter is None:
        return
    required = analysis.context_type
    if _satisfies(get_origin(declared) or declared, required) is not False:
        return
    raise ContextTypeError(
        f"{describe_callable(analysis.target)} requires {_name(required)}, but this deck provides "
        f"{_name(declared)}. Declare Deck(context=...) with a type {_name(required)} accepts, or widen "
        f"the annotation on parameter {analysis.context_parameter!r}."
    )


def _satisfies(declared: object, required: object) -> bool | None:
    """Whether ``declared`` meets ``required`` — or ``None`` when the runtime cannot say.

    ``None`` is the whole reason this returns three values rather than two: a structural
    ``Protocol`` that ``issubclass`` refuses to decide on, a ``TypeVar``, an annotation that is
    not a class at all. Reporting those as incompatible would refuse a build that is very likely
    correct, and reporting them as compatible would claim a guarantee nobody checked.
    """
    if required is Any or declared is Any:
        return True
    if get_origin(required) in _UNION_FORMS:
        arms = [_satisfies(declared, arm) for arm in get_args(required)]
        if True in arms:
            return True
        return None if None in arms else False
    # `Mapping[str, Any]` is not a class and `issubclass` rejects it outright; its origin is the
    # runtime ABC, which is the part of the annotation the runtime can actually check.
    target = get_origin(required) or required
    if not isinstance(declared, type) or not isinstance(target, type):
        return None
    try:
        return issubclass(declared, target)
    except TypeError:
        return None


def _name(annotation: object) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def describe_callable(target: Callable[..., object]) -> str:
    """How every message about ``target`` names it — the author's own name for the function,
    so an error points at their code rather than at whatever AgentDeck compiled it into."""
    return getattr(target, "__qualname__", None) or repr(target)


__all__ = [
    "CallableAnalysis",
    "analyze_callable",
    "check_context_type",
    "declared_context_type",
    "describe_callable",
]
