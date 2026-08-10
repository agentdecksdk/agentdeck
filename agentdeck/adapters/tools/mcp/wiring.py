"""Wiring MCP servers into agents, and the strict-protocol banner.

Turns an agent's declared server names into the SDK instances to attach, and —
when some are unavailable — a prompt banner that makes the model report the gap
instead of fabricating an answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from agents.mcp import MCPServer


def resolve_agent_mcp_status(names: Iterable[str]) -> tuple[list[MCPServer], list[str]]:
    """Split declared names into ``(available_servers, missing_names)``.

    "Missing" = unknown to the registry or its connect failed. ``authoring.compile.compile_agent``
    feeds the missing list to :func:`mcp_status_banner`.
    """
    available: list[MCPServer] = []
    missing: list[str] = []
    for name in names:
        server = MCPLifecycle.resolve_or_pending(name)
        if server is None or not MCPLifecycle.is_available(name):
            missing.append(name)
        else:
            available.append(server)
    return available, missing


def resolve_agent_mcp_servers(names: Iterable[str]) -> list[MCPServer]:
    """The available servers for ``names`` (missing ones dropped)."""
    return resolve_agent_mcp_status(names)[0]


def mcp_status_banner(missing: Sequence[str]) -> str:
    """Strict-protocol banner prepended to an agent's instructions when servers are down.

    Empty when nothing is missing — keeps the happy-path prompt bit-identical so
    prompt-cache hits are unaffected.
    """
    if not missing:
        return ""
    names = ", ".join(f"`{n}`" for n in missing)
    return (
        "## Runtime MCP status\n\n"
        f"The following MCP server(s) you depend on are UNAVAILABLE: {names}.\n"
        "Tools from those servers are NOT exposed to you in this turn.\n"
        "If the user's request requires those tools, reply with exactly:\n\n"
        "    error: mcp_unavailable: <server-name>\n\n"
        "and stop. Do not invent results. Do not retry. Do not call other "
        "tools as a substitute unless the user explicitly asks for an "
        "alternative.\n\n---\n\n"
    )


__all__ = ["mcp_status_banner", "resolve_agent_mcp_servers", "resolve_agent_mcp_status"]
