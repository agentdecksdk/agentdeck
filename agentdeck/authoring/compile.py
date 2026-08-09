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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent as SDKAgent
from agents import ModelSettings

from agentdeck.adapters.engines.langgraph.checkpointer import resolve_checkpointer
from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_status
from agentdeck.errors import ConfigError, NotFoundError
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agents.tool import FunctionTool
    from langgraph.graph.state import CompiledStateGraph

    from agentdeck.authoring.agent import Agent
    from agentdeck.authoring.workflow import Workflow


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
    return graph.compile(checkpointer=resolve_checkpointer(checkpoint.backend, checkpoint.url))


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
    instructions, mcp_servers = _resolve_mcp(agent)
    tools = list(agent.tools)
    if agent.skills:
        if resolve_skills is None:
            raise ConfigError(
                f"agent {agent.name!r} declares skills={list(agent.skills)!r}, but no skill root is "
                "configured — pass skills=... to Deck(...)."
            )
        disclosure, skill_tools = resolve_skills(agent.skills)
        instructions = instructions + disclosure
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
        else:
            resolved_tools.append(tool)
    # Fields the SDK's own dataclass defaults (empty list, `None`) apply to: passing `None`
    # explicitly for `tools`/`mcp_servers` fails its `__post_init__` type check, so an unset
    # value is omitted from the call entirely rather than passed through as `None`.
    fields: dict[str, Any] = {
        "name": agent.name,
        "instructions": instructions,
        "handoff_description": agent.handoff_description,
        "model": agent.model,
        "model_settings": ModelSettings(**agent.model_settings) if agent.model_settings else None,
        "tools": resolved_tools or None,
        "output_type": agent.output_type,
        "hooks": agent.hooks,
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


def _resolve_mcp(agent: Agent) -> tuple[str, list[Any]]:
    """Instructions with the strict-protocol banner prepended (empty on the happy path, so
    prompt caches stay warm), and the SDK servers to attach — unchanged from v1's own
    per-agent MCP resolution, since it has never needed anything but ``MCPLifecycle``'s state.
    """
    if not agent.mcp:
        return agent.instructions, []
    available, missing = resolve_agent_mcp_status(agent.mcp)
    banner = mcp_status_banner(missing)
    instructions = banner + agent.instructions if banner else agent.instructions
    return instructions, list(available)


__all__ = ["compile_agent", "compile_workflow", "link_handoffs"]
