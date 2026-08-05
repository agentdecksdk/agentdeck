"""The MCP tool adapter: server registry, hardened transport, agent wiring."""

from __future__ import annotations

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle, load_mcp_config
from agentdeck.adapters.tools.mcp.transport import MCPServerStreamableHttpResilient
from agentdeck.adapters.tools.mcp.wiring import mcp_status_banner, resolve_agent_mcp_servers, resolve_agent_mcp_status

__all__ = [
    "MCPLifecycle",
    "MCPServerStreamableHttpResilient",
    "load_mcp_config",
    "mcp_status_banner",
    "resolve_agent_mcp_servers",
    "resolve_agent_mcp_status",
]
