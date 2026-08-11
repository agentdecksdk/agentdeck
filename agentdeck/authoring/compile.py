"""Compile :class:`~agentdeck.authoring.agent.Agent` declarations into SDK-native ``agents.Agent``
objects — the one place authoring's data classes turn into what an engine actually runs.

Handoffs are a second pass over :func:`compile_agent`'s output, not part of it: agent A's
handoff to agent B needs B's own compiled object, which may not exist yet while A is being
built (and a cycle, A -> B -> A, never will on a single pass). ``link_handoffs`` runs once every
agent in a catalog has a bare compiled form, and mutates ``.handoffs`` in place — the SDK's own
``Agent`` is mutable, so this is the same shape v1's recursive handoff resolution used.

MCP status stays wired straight to the process-wide :class:`MCPLifecycle`, unchanged from v1:
resolving servers has never needed a catalog, only the lifecycle's own state. Skills and
workflow-as-tool *do* need one — a root to scan, a graph to call — so both arrive as optional
resolver callbacks a ``Deck`` supplies; an ``Agent`` built with neither configured raises a
clear ``ConfigError`` naming what is missing, instead of silently dropping what it declared.

A bare callable in ``tools=`` is **compiled** here, by ``tools.compile_tool`` — a plain function
is the canonical way to declare a tool, and a function annotated ``Context[...]`` can only be
declared that way, since ``@function_tool`` applied by the author would put the context parameter
in the model-visible schema. This used to be a rejection ("wrap it with ``@function_tool``"), for
the good reason that an uncompiled callable reached the SDK and failed mid-run with a ``UserError``
about hosted tools; compiling it here keeps that failure from happening while giving the callable
a real contract. A pre-built SDK tool object is still accepted and passed straight through, as
engine-native: nothing here introspects it, and it carries no portability guarantee.

``instructions=`` and ``hooks=`` go through that same compiler rather than a mechanism each:
a callable in ``instructions=`` becomes the SDK's dynamic-instructions shape, and a hooks object
whose methods declare ``Context[...]`` has those methods bridged. Both are no-ops for what was
already accepted — a plain string, and hooks that name the SDK's own wrapper.

``refresh_mcp_status`` is a second pass over MCP status specifically, the same shape as
``link_handoffs`` — needed because ``Deck.build()`` compiles agents before ``Deck.__aenter__``
ever connects a server, so the first resolution is always stale by the time anything runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args, get_origin

from agents import Agent as SDKAgent
from agents import ModelSettings
from agents import Tool as SDKTool

from agentdeck.adapters.engines.langgraph.checkpointer import resolve_checkpointer
from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_status
from agentdeck.authoring.hooks import compile_hooks
from agentdeck.authoring.instructions import compile_instructions
from agentdeck.authoring.tools import compile_tool
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.settings import get_settings, parse_backend_url

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agents.tool import FunctionTool
    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.authoring.agent import Agent
    from agentdeck.authoring.workflow import Workflow

# `agents.Tool` is a `Union` of concrete SDK tool classes (`FunctionTool`, `WebSearchTool`, a
# hosted computer/shell tool, ...) rather than a class of its own, so `isinstance(x, SDKTool)`
# does not work directly — one member, `ComputerTool[Any]`, is a subscripted generic, which
# `isinstance` also rejects outright. `get_origin(a) or a` unwraps that one case (and is a
# no-op for the rest) so this tuple is exactly the concrete classes `isinstance` can check
# against, computed once rather than on every tool.
_SDK_TOOL_TYPES: tuple[type[Any], ...] = tuple(get_origin(a) or a for a in get_args(SDKTool))


def compile_workflow(workflow: Workflow) -> CompiledStateGraph[Any]:
    """Compile ``workflow``'s graph, with a checkpointer if it is ``durable``.

    Every call recompiles rather than caching: unlike v1's ``BaseWorkflow.build()`` (a
    ``ClassVar`` cache on the declaring class), an immutable ``Workflow`` instance has nowhere
    to stash one without breaking the freeze guarantee, and compiling a graph is cheap next to
    opening the checkpointer connection it wraps.
    """
    graph = workflow.build_graph()
    if not workflow.durable:
        return graph.compile()
    checkpoint = get_settings().checkpoint
    scheme, rest = parse_backend_url(checkpoint.url)
    backend = "postgres" if scheme == "postgresql" else scheme
    path_or_dsn = rest if backend == "sqlite" else checkpoint.url
    return graph.compile(checkpointer=resolve_checkpointer(backend, path_or_dsn))


def compile_agent(
    agent: Agent,
    *,
    resolve_skills: Callable[[Sequence[str]], tuple[str, Sequence[FunctionTool]]] | None = None,
    resolve_workflow_tool: Callable[[Workflow], FunctionTool] | None = None,
) -> SDKAgent:
    """Build the SDK ``Agent`` for ``agent``, minus handoffs (see module docstring).

    Raises :class:`ConfigError` rather than silently dropping ``skills=``/a workflow tool when
    no resolver was supplied — the caller (``Deck.build()``, or a bare compile with neither
    configured) must be the one to say why, not the compiled agent by omission.
    """
    banner, mcp_servers = _resolve_mcp(agent)
    disclosure = ""
    tools = list(agent.tools)
    if agent.skills:
        if resolve_skills is None:
            raise ConfigError(
                f"agent {agent.name!r} declares skills={list(agent.skills)!r}, but no skill root is "
                "configured — pass skills=... to Deck(...)."
            )
        disclosure, skill_tools = resolve_skills(agent.skills)
        tools.extend(skill_tools)
    from agentdeck.authoring.workflow import Workflow

    resolved_tools: list[Any] = []
    for tool in tools:
        if isinstance(tool, Workflow):
            if resolve_workflow_tool is None:
                raise ConfigError(
                    f"agent {agent.name!r} uses workflow {tool.name!r} as a tool, but no workflow "
                    "catalog is configured — pass workflows=... to Deck(...)."
                )
            resolved_tools.append(resolve_workflow_tool(tool))
        elif isinstance(tool, _SDK_TOOL_TYPES):
            resolved_tools.append(tool)
        elif callable(tool):
            try:
                resolved_tools.append(compile_tool(tool))
            except ConfigError as refused:
                raise ConfigError(f"agent {agent.name!r}: {refused}") from refused
        else:
            raise ConfigError(
                f"agent {agent.name!r} has a tool that is neither a callable nor an Agents SDK tool object: {tool!r}."
            )
    # Fields the SDK's own dataclass defaults (empty list, `None`) apply to: passing `None`
    # explicitly for `tools`/`mcp_servers` fails its `__post_init__` type check, so an unset
    # value is omitted from the call entirely rather than passed through as `None`.
    fields: dict[str, Any] = {
        "name": agent.name,
        "instructions": _instructions(agent, banner, disclosure),
        "handoff_description": agent.handoff_description,
        "model": agent.model,
        "model_settings": ModelSettings(**agent.model_settings) if agent.model_settings else None,
        "tools": resolved_tools or None,
        "output_type": agent.output_type,
        "hooks": compile_hooks(agent.hooks),
        "mcp_servers": mcp_servers or None,
    }
    sdk_agent = SDKAgent(**{k: v for k, v in fields.items() if v is not None})
    sdk_agent.handoffs = []
    return sdk_agent


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
    """Re-resolve MCP status in place, on an already-compiled agent — a second pass over
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
        # baked in here to correct — only a plain string carries a stale one.
        if isinstance(stale_instructions, str):
            # At build time every declared name resolved as missing (nothing had connected yet),
            # so the banner baked in is this exact, deterministic text — strip only that prefix,
            # so a skills disclosure compile_agent appended after it survives untouched.
            declared = str(agent.instructions)
            stale_prefix = mcp_status_banner(list(agent.mcp)) + declared
            sdk_agent.instructions = banner + declared + stale_instructions[len(stale_prefix) :]
        sdk_agent.mcp_servers = mcp_servers


def _instructions(agent: Agent, banner: str, disclosure: str) -> Any:
    """What the SDK agent's ``instructions`` field becomes: the composed string, or the
    dynamic-instructions callable that composes the same three parts per turn.

    A callable composes at call time rather than at compile time so its MCP banner is the live
    one — :func:`refresh_mcp_status`'s prefix surgery works on a string it can measure, and a
    closure has no prefix to measure.
    """
    if not callable(agent.instructions):
        return banner + agent.instructions + disclosure
    compiled = compile_instructions(agent.instructions)

    async def instructions(wrapper: Any, sdk_agent: Any) -> str:
        fresh, _ = _resolve_mcp(agent)
        return fresh + str(await compiled(wrapper, sdk_agent)) + disclosure

    return instructions


def _resolve_mcp(agent: Agent) -> tuple[str, list[Any]]:
    """The strict-protocol banner to prepend (empty on the happy path, so prompt caches stay
    warm), and the SDK servers to attach — unchanged from v1's own per-agent MCP resolution,
    since it has never needed anything but ``MCPLifecycle``'s state.
    """
    if not agent.mcp:
        return "", []
    available, missing = resolve_agent_mcp_status(agent.mcp)
    return mcp_status_banner(missing), list(available)


__all__ = ["compile_agent", "compile_workflow", "link_handoffs", "refresh_mcp_status"]
