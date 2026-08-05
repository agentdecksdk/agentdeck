"""``ToolSourcePort`` over the MCP server registry.

An invocable names the servers it wants in ``InvocableSpec.metadata``; this hands back the
ones that are connected, the names of the ones that are not, and the banner that makes a
model report the gap. Unknown, unconfigured and unreachable all resolve the same way — a
:class:`ToolSet` — because MCP being down degrades a run, never fails it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_status
from agentdeck.core.ports.tools import ToolSet, ToolSourcePort

if TYPE_CHECKING:
    from agentdeck.core.invocable import InvocableSpec

MCP_SERVER_NAMES_KEY = "mcp_server_names"
"""``InvocableSpec.metadata`` key holding the MCP server names an invocable declares."""


class MCPToolSource(ToolSourcePort):
    """The project's MCP servers, as one tool source.

    Stateless: the servers themselves live in :class:`MCPLifecycle`, which the composition
    root connects at startup and closes at shutdown — resolving never connects, so it is
    safe to call while building a prompt. Not free, though: on a cold cache the first call
    materialises process settings, which reads the config files off disk.
    """

    def resolve(self, spec: InvocableSpec) -> ToolSet:
        declared = spec.metadata.get(MCP_SERVER_NAMES_KEY) or ()
        if isinstance(declared, str):  # a lone name, not a list of characters
            declared = (declared,)
        # metadata is free-form, so nothing has checked these are strings yet; a name that
        # isn't one is reported missing rather than exploding somewhere further in.
        declared = tuple(str(name) for name in declared)
        available, missing = resolve_agent_mcp_status(declared)
        return ToolSet(
            tools=tuple(available),
            unavailable=tuple(missing),
            notice=mcp_status_banner(missing),
        )


__all__ = ["MCP_SERVER_NAMES_KEY", "MCPToolSource"]
