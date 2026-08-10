"""The MCP tool adapter: ``ToolSourcePort`` over an MCP server registry, transport and all."""

from __future__ import annotations

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle
from agentdeck.adapters.tools.mcp.source import MCP_SERVER_NAMES_KEY, MCPToolSource
from agentdeck.adapters.tools.mcp.transport import MCPServerStreamableHttpResilient
from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_servers, resolve_agent_mcp_status

__all__ = [
    "MCP_SERVER_NAMES_KEY",
    "MCPLifecycle",
    "MCPServerStreamableHttpResilient",
    "MCPToolSource",
    "mcp_status_banner",
    "resolve_agent_mcp_servers",
    "resolve_agent_mcp_status",
]
