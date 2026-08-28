"""Compile :class:`~agentdeck.authoring.agent.Agent` declarations into SDK-native ``agents.Agent``
objects  -  the one place authoring's data classes turn into what an engine actually runs.

Handoffs are a second pass over :func:`compile_agent`'s output, not part of it: agent A's
handoff to agent B needs B's own compiled object, which may not exist yet while A is being
built (and a cycle, A -> B -> A, never will on a single pass). ``link_handoffs`` runs once every
agent in a catalog has a bare compiled form, and mutates ``.handoffs`` in place  -  the SDK's own
``Agent`` is mutable, so this is the same shape v1's recursive handoff resolution used.

MCP status stays wired straight to the process-wide :class:`MCPLifecycle`, unchanged from v1:
resolving servers has never needed a catalog, only the lifecycle's own state. Skills *do* need
one  -  a root to scan  -  so it arrives as an optional resolver callback a ``Deck`` supplies; an
``Agent`` built without it raises a clear ``ConfigError`` naming what is missing, instead of
silently dropping what it declared.

A bare callable in ``tools=`` is **compiled** here, by ``tools.compile_tool``  -  a plain function
is the canonical way to declare a context-free tool. One that declares ``ToolCtx[...]`` needs
``@tool`` instead: ``@function_tool`` applied by the author would put the context parameter in
the model-visible schema, so a plain function carrying one is refused here, naming the decorator
to add, rather than silently compiled the way ``@tool``'s own callable still is (below). This
used to be a rejection ("wrap it with ``@function_tool``"), for the good reason that an
uncompiled callable reached the SDK and failed mid-run with a ``UserError`` about hosted tools;
compiling it here keeps that failure from happening while giving the callable a real contract. A
pre-built SDK tool object is still accepted and passed straight through, as
engine-native: nothing here introspects it, and it carries no portability guarantee. That
includes the failure formatter ``compile_tool`` attaches (#250)  -  a tool the author decorated
with ``@function_tool`` themselves keeps whatever ``failure_error_function`` they chose, so its
exceptions stay off ``tool.call.completed.error``. Passing one is opting out of what compiling
buys, which is the same trade the sentence above names, not a second one.

``instructions=`` and ``hooks=`` go through that same compiler rather than a mechanism each:
a callable in ``instructions=`` becomes the SDK's dynamic-instructions shape, and a hooks object
whose methods declare ``ToolCtx[...]`` has those methods bridged. Both are no-ops for what was
already accepted  -  a plain string, and hooks that name the SDK's own wrapper.

``refresh_mcp_status`` is a second pass over MCP status specifically, the same shape as
``link_handoffs``  -  needed because ``Deck.build()`` compiles agents before ``Deck.__aenter__``
ever connects a server, so the first resolution is always stale by the time anything runs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, get_args, get_origin

from agents import Agent as SDKAgent
from agents import ModelSettings
from agents import Tool as SDKTool

from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_status
from agentdeck.authoring.hooks import compile_hooks
from agentdeck.authoring.instructions import compile_instructions
from agentdeck.authoring.native import NativeDefinition
from agentdeck.authoring.native import tool as _native_tool
from agentdeck.authoring.tools import compile_tool
from agentdeck.core.context import ToolCtx  # noqa: TC001  -  a subagent tool's own annotation, resolved at runtime
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from agents.tool import FunctionTool

    from agentdeck.authoring.agent import Agent
    from agentdeck.core.context import RunContext
    from agentdeck.core.workers import SyncToolWorkers

type Delegate = Callable[["RunContext", str, str], "Awaitable[Any]"]
"""How a subagent tool reaches the deck: ``delegate(parent_context, subagent_name, task)``, run as
a child of the turn that called it and awaited for its final output.

A callable rather than a protocol, for :data:`~agentdeck.core.context.Invoker`'s reason  -  one
method is not an interface  -  and the deck's own ``ctx.invoke`` underneath, so there is no second
execution path for a delegation to take."""

# `agents.Tool` is a `Union` of concrete SDK tool classes (`FunctionTool`, `WebSearchTool`, a
# hosted computer/shell tool, ...) rather than a class of its own, so `isinstance(x, SDKTool)`
# does not work directly  -  one member, `ComputerTool[Any]`, is a subscripted generic, which
# `isinstance` also rejects outright. `get_origin(a) or a` unwraps that one case (and is a
# no-op for the rest) so this tuple is exactly the concrete classes `isinstance` can check
# against, computed once rather than on every tool.
_SDK_TOOL_TYPES: tuple[type[Any], ...] = tuple(get_origin(a) or a for a in get_args(SDKTool))


def compile_agent(
    agent: Agent,
    *,
    resolve_skills: Callable[[Sequence[str]], tuple[str, Sequence[FunctionTool]]] | None = None,
    context_type: object | None = None,
    catalog: Mapping[str, Agent] | None = None,
    delegate: Delegate | None = None,
    workers: SyncToolWorkers | None = None,
) -> SDKAgent:
    """Build the SDK ``Agent`` for ``agent``, minus handoffs (see module docstring).

    Raises :class:`ConfigError` rather than silently dropping ``skills=`` when no resolver was
    supplied  -  the caller (``Deck.build()``, or a bare compile with none configured) must be the
    one to say why, not the compiled agent by omission.

    ``context_type`` is the owning deck's ``Deck(context=...)`` declaration, checked against
    every ``ToolCtx[...]`` this agent's tools, instructions and hooks require.

    ``catalog`` and ``delegate`` are what ``subagents=`` needs and a standalone compile has
    neither of: the agents a name may resolve to, and the deck's own way of starting a child run.

    ``workers`` is the deck's shared sync-tool worker pool, threaded to :func:`compile_tool` the
    same way; ``None`` for a standalone compile, same fallback.
    """
    banner, mcp_servers = _resolve_mcp(agent)
    disclosure = ""
    tools = [*agent.tools, *_subagent_tools(agent, catalog, delegate)]
    if agent.skills:
        if resolve_skills is None:
            raise ConfigError(
                f"agent {agent.name!r} declares skills={list(agent.skills)!r}, but no skill root is "
                "configured  -  pass skills=... to Deck(...)."
            )
        disclosure, skill_tools = resolve_skills(agent.skills)
        tools.extend(skill_tools)
    resolved_tools: list[Any] = []
    for tool in tools:
        if isinstance(tool, _SDK_TOOL_TYPES):
            resolved_tools.append(tool)
            continue
        if not isinstance(tool, NativeDefinition) and not callable(tool):
            raise ConfigError(
                f"agent {agent.name!r} has a tool that is neither a callable nor an Agents SDK tool object: {tool!r}."
            )
        try:
            if isinstance(tool, NativeDefinition):
                # A ``@tool`` is compiled from the function it was declared over: the definition is
                # what the catalog holds and what makes it invocable in its own right, and the SDK
                # only ever needed the callable underneath. Only this branch may pass a context:
                # it is the one callable shape that declared it through ``@tool``.
                resolved_tools.append(
                    compile_tool(tool.call, context_type=context_type, declared_via_tool=True, workers=workers)
                )
            else:
                resolved_tools.append(compile_tool(tool, context_type=context_type, workers=workers))
        except ConfigError as refused:
            # Re-raised as its own class: a ContextTypeError flattened to its supertype here
            # would reach the caller as a different error than the one the API promises.
            raise type(refused)(f"agent {agent.name!r}: {refused}") from refused
    # Fields the SDK's own dataclass defaults (empty list, `None`) apply to: passing `None`
    # explicitly for `tools`/`mcp_servers` fails its `__post_init__` type check, so an unset
    # value is omitted from the call entirely rather than passed through as `None`.
    fields: dict[str, Any] = {
        "name": agent.name,
        "instructions": _instructions(agent, banner, disclosure, context_type),
        "handoff_description": agent.handoff_description,
        # An agent that names no model still needs one: `RunConfig.model` is never set
        # (`adapters/executors/openai_agents/runconfig.py`), since the SDK treats that field as
        # an override of every agent's own model, declared or not. Resolving the default here
        # instead means an agent's own `model=` always wins, whatever else the run is playing.
        "model": agent.model if agent.model is not None else get_settings().openai.model,
        "model_settings": ModelSettings(**agent.model_settings) if agent.model_settings else None,
        "tools": resolved_tools or None,
        "output_type": agent.output_type,
        "hooks": compile_hooks(agent.hooks, context_type=context_type),
        "mcp_servers": mcp_servers or None,
    }
    sdk_agent = SDKAgent(**{k: v for k, v in fields.items() if v is not None})
    sdk_agent.handoffs = []
    return sdk_agent


def _subagent_tools(agent: Agent, catalog: Mapping[str, Agent] | None, delegate: Delegate | None) -> list[Any]:
    """One callable per declared subagent, for the loop above to compile like any other tool.

    Resolved against the catalog here rather than validated somewhere else and resolved here: the
    schema the model is shown names the subagent, so an unknown name has no tool to build and
    :func:`link_handoffs`'s own lookup failure is the shape it is reported in.
    """
    if not agent.subagents:
        return []
    if catalog is None or delegate is None:
        raise ConfigError(
            f"agent {agent.name!r} declares subagents={list(agent.subagents)!r}, which resolve "
            f"against a catalog and run as child runs of this one; a standalone compile has "
            f"neither. Put it in Deck(agents=[...]) and build that."
        )
    return [_delegation(_lookup_agent(catalog, name), delegate) for name in agent.subagents]


def _delegation(subagent: Agent, delegate: Delegate) -> NativeDefinition:
    """The tool the model calls to hand ``subagent`` a task and wait for what it comes back with.

    Declares ``ToolCtx[...]``, so it is synthesized as a ``@tool`` itself  -  the same declaration
    an author's own context-carrying tool needs  -  and the run it is inside reaches the deck
    through the seam ``ctx.invoke`` already uses: a delegation is a child run, not a second way to
    execute something.
    """

    async def delegated(ctx: ToolCtx[Any], task: str) -> Any:
        return await delegate(ctx._run, subagent.name, task)  # noqa: SLF001  -  the carrier this view is of

    delegated.__name__ = f"delegate_to_{_identifier(subagent.name)}"
    delegated.__doc__ = subagent.handoff_description or (
        f"Delegate one self-contained task to the {subagent.name} agent and wait for its result."
    )
    return _native_tool(delegated)


def _identifier(name: str) -> str:
    """An agent name as a tool name can carry it: the SDK's schema names are identifiers and an
    agent's is free text."""
    return re.sub(r"\W+", "_", name).strip("_") or "subagent"


def _lookup_agent(catalog: Mapping[str, Agent], name: str) -> Agent:
    try:
        return catalog[name]
    except KeyError:
        raise NotFoundError(f"No agent named {name!r}. Available: {sorted(catalog)}.") from None


def link_handoffs(compiled: Mapping[str, SDKAgent], agents: Sequence[Agent]) -> None:
    """Fill in ``.handoffs`` on every agent in ``compiled``, once all of them exist.

    A handoff entry is a name (resolved against ``compiled``), an already-compiled SDK object,
    or an :class:`Agent` from the same catalog (resolved by its ``.name``). Unknown names raise
    :class:`NotFoundError` naming what was available, matching v1's own message.
    """
    from agentdeck.authoring.agent import Agent as AuthoringAgent

    for agent in agents:
        resolved: list[Any] = []
        for entry in agent.handoffs:
            if isinstance(entry, str):
                resolved.append(_lookup(compiled, entry))
            elif isinstance(entry, AuthoringAgent):
                resolved.append(_lookup(compiled, entry.name))
            else:
                resolved.append(entry)  # already SDK-native: Agent or Handoff
        compiled[agent.name].handoffs = resolved


def _lookup(compiled: Mapping[str, SDKAgent], name: str) -> SDKAgent:
    try:
        return compiled[name]
    except KeyError:
        raise NotFoundError(f"No agent named {name!r}. Available: {sorted(compiled)}.") from None


def refresh_mcp_status(compiled: Mapping[str, SDKAgent], agents: Sequence[Agent]) -> None:
    """Re-resolve MCP status in place, on an already-compiled agent  -  a second pass over
    :func:`compile_agent`'s output, same shape as :func:`link_handoffs`.

    ``compile_agent`` resolves each agent's ``mcp=`` against :class:`MCPLifecycle` once, at
    compile time. Inside ``Deck.build()`` that runs before ``Deck.__aenter__`` has connected
    anything, so every declared server bakes in as missing regardless of what it will actually
    be once open. ``Deck.__aenter__`` calls this right after ``MCPLifecycle.startup`` connects
    the real servers, so the compiled agent that actually runs turns carries the corrected tools
    and banner instead of the stale ones from build time.
    """
    for agent in agents:
        if not agent.mcp:
            continue
        sdk_agent = compiled[agent.name]
        stale_instructions = sdk_agent.instructions
        banner, mcp_servers = _resolve_mcp(agent)
        # Compiled instructions resolve their own banner on every turn, so there is nothing
        # baked in here to correct  -  only a plain string carries a stale one.
        if isinstance(stale_instructions, str):
            # At build time every declared name resolved as missing (nothing had connected yet),
            # so the banner baked in is this exact, deterministic text  -  strip only that prefix,
            # so a skills disclosure compile_agent appended after it survives untouched.
            declared = str(agent.instructions)
            stale_prefix = mcp_status_banner(list(agent.mcp)) + declared
            sdk_agent.instructions = banner + declared + stale_instructions[len(stale_prefix) :]
        sdk_agent.mcp_servers = mcp_servers


def _instructions(agent: Agent, banner: str, disclosure: str, context_type: object | None = None) -> Any:
    """What the SDK agent's ``instructions`` field becomes: the composed string, or the
    dynamic-instructions callable that composes the same three parts per turn.

    A callable composes at call time rather than at compile time so its MCP banner is the live
    one  -  :func:`refresh_mcp_status`'s prefix surgery works on a string it can measure, and a
    closure has no prefix to measure.
    """
    if not callable(agent.instructions):
        return banner + agent.instructions + disclosure
    compiled = compile_instructions(agent.instructions, context_type=context_type)

    async def instructions(wrapper: Any, sdk_agent: Any) -> str:
        banner_now, _ = _resolve_mcp(agent)
        # Concatenated rather than coerced: a callable that returned something other than a
        # string has a bug, and a silent ``str()`` here would put its repr in the prompt.
        return banner_now + await compiled(wrapper, sdk_agent) + disclosure

    return instructions


def _resolve_mcp(agent: Agent) -> tuple[str, list[Any]]:
    """The strict-protocol banner to prepend (empty on the happy path, so prompt caches stay
    warm), and the SDK servers to attach  -  unchanged from v1's own per-agent MCP resolution,
    since it has never needed anything but ``MCPLifecycle``'s state.
    """
    if not agent.mcp:
        return "", []
    available, missing = resolve_agent_mcp_status(agent.mcp)
    return mcp_status_banner(missing), list(available)


__all__ = ["Delegate", "compile_agent", "link_handoffs", "refresh_mcp_status"]
