"""MCP transport, lifecycle registry, and agent-wiring — see the submodules."""

from __future__ import annotations

from agentdeck.agents.mcp.lifecycle import MCPLifecycle, load_mcp_config
from agentdeck.agents.mcp.transport import MCPServerStreamableHttpResilient
from agentdeck.agents.mcp.wiring import mcp_status_banner, resolve_agent_mcp_servers, resolve_agent_mcp_status

__all__ = [
    "MCPLifecycle",
    "MCPServerStreamableHttpResilient",
    "load_mcp_config",
    "mcp_status_banner",
    "resolve_agent_mcp_servers",
    "resolve_agent_mcp_status",
]
