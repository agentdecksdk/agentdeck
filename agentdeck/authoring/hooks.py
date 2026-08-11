"""Compile an agent's lifecycle hooks so a hook method can declare ``Context[...]`` too.

The fourth injection site, and the last one, through the same analysis as
:mod:`agentdeck.authoring.tools` and :mod:`agentdeck.authoring.instructions` — a hook that
wanted the run's environment would otherwise have to name ``RunContextWrapper``, and an
application's own hook class would stop being portable at the one place it is easiest to leak
an engine type into.

Narrow by construction: only the SDK's own hook methods are considered, only the ones the
author actually overrode, and only those declaring a ``Context[...]`` parameter are rewritten.
A hooks object with none is returned exactly as it was given — engine-native, introspected by
nothing, so an application already writing SDK hooks keeps every behavior it has.

The context parameter must be the hook's **first** parameter, where the SDK's own wrapper goes:
the SDK calls hooks positionally, and a substitution anywhere else could only be a guess about
which of the remaining arguments was meant.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from agents.lifecycle import AgentHooks

from agentdeck.authoring.injection import analyze_callable, describe_callable
from agentdeck.core.context import Context, RunContext
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

HOOK_METHODS: frozenset[str] = frozenset(
    name for name, _ in inspect.getmembers(AgentHooks, inspect.isfunction) if not name.startswith("_")
)
"""Every lifecycle method the installed SDK defines — read off the class rather than listed
here, so a hook the SDK adds is bridged without this module being edited to notice."""


def compile_hooks(hooks: Any) -> Any:
    """Return ``hooks`` with each ``Context[...]``-declaring method bridged, or unchanged.

    Raises :class:`ConfigError` naming the method when its context parameter is not first, or
    when it declares more than one (through the shared analysis).
    """
    if hooks is None:
        return None
    bridged: dict[str, Callable[..., Any]] = {}
    for name in sorted(HOOK_METHODS):
        method = getattr(hooks, name, None)
        if method is None or getattr(AgentHooks, name, None) is getattr(type(hooks), name, None):
            continue  # not overridden: the SDK's own no-op, with nothing to inject into
        try:
            analysis = analyze_callable(method)
        except ConfigError as refused:
            raise ConfigError(f"{_describe(hooks)}.{name}: {refused}") from refused
        if not analysis.reliable or analysis.context_parameter is None:
            continue
        first = next(iter(inspect.signature(inspect.unwrap(method)).parameters), None)
        if first != analysis.context_parameter:
            raise ConfigError(
                f"{_describe(hooks)}.{name} declares its Context[...] parameter as "
                f"{analysis.context_parameter!r}, but a hook receives it first — where the SDK passes "
                f"its own context object. Move it ahead of {first!r}."
            )
        bridged[name] = _bridge(method)
    return _BridgedHooks(hooks, bridged) if bridged else hooks


class _BridgedHooks(AgentHooks[Any]):
    """``hooks`` with the bridged methods bound over it.

    An ``AgentHooks`` subclass rather than a bare object because that is the type the SDK's
    ``Agent`` field declares; the methods are set per instance, where they shadow the class's
    own no-ops, so nothing here has to enumerate a hook signature it would then have to keep
    in step with the SDK.
    """

    def __init__(self, target: Any, bridged: dict[str, Callable[..., Any]]) -> None:
        for name in HOOK_METHODS:
            forwarded = bridged.get(name) or getattr(target, name, None)
            if forwarded is not None:
                setattr(self, name, forwarded)


def _bridge(method: Callable[..., Any]) -> Callable[..., Any]:
    """The hook the SDK calls: its own wrapper first, unwrapped to a ``Context`` before the
    author's method runs. Every later argument travels through untouched."""
    named = describe_callable(method)

    async def hook(wrapper: Any, *rest: Any, **kwargs: Any) -> Any:
        run = wrapper.context
        if not isinstance(run, RunContext):
            raise ConfigError(
                f"{named} declares a Context[...] parameter, but this run carries "
                f"{type(run).__name__} rather than an AgentDeck run context — hooks compiled by "
                "AgentDeck have to be played by an AgentDeck run."
            )
        result = method(Context(run), *rest, **kwargs)
        return await result if inspect.isawaitable(result) else result

    hook.__name__ = getattr(method, "__name__", "hook")
    hook.__qualname__ = named
    return hook


def _describe(hooks: Any) -> str:
    return type(hooks).__name__


__all__ = ["HOOK_METHODS", "compile_hooks"]
