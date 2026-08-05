"""Legacy path, kept working: the registry itself is
``agentdeck.adapters.tools.mcp.lifecycle``.

Its own module (not just the package re-export) because v1 callers import this dotted path
directly. The names below are the same objects, so patching one patches the other.
"""

from __future__ import annotations

from agentdeck.adapters.tools.mcp.lifecycle import MCPLifecycle, load_mcp_config

__all__ = ["MCPLifecycle", "load_mcp_config"]
