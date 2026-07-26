"""Declarative wrapper over the OpenAI Agents SDK."""

from agentdeck.agents.base import BaseAgent, BaseSandboxAgent
from agentdeck.agents.capabilities import CapabilitiesSpec, CompactionSpec, FilesystemSpec, MemorySpec, ShellSpec
from agentdeck.agents.mcp import (
    MCPLifecycle,
    MCPServerStreamableHttpResilient,
    load_mcp_config,
    mcp_status_banner,
    resolve_agent_mcp_servers,
    resolve_agent_mcp_status,
)
from agentdeck.agents.registry import AgentRegistry
from agentdeck.agents.runners import BaseRunner, HeadlessRunner
from agentdeck.agents.web_search import web_search

__all__ = [
    "AgentRegistry",
    "BaseAgent",
    "BaseRunner",
    "BaseSandboxAgent",
    "CapabilitiesSpec",
    "CompactionSpec",
    "FilesystemSpec",
    "HeadlessRunner",
    "MCPLifecycle",
    "MCPServerStreamableHttpResilient",
    "MemorySpec",
    "ShellSpec",
    "load_mcp_config",
    "mcp_status_banner",
    "resolve_agent_mcp_servers",
    "resolve_agent_mcp_status",
    "web_search",
]
