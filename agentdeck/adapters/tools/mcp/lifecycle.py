"""MCP server registry + process lifecycle, driven by the shared settings config.

Agents name the servers they need; this owns how to reach them. Servers are the
``mcp.servers`` settings group (packaged default in ``config.default.yaml``),
keyed by name and overridable via ``AGENTDECK_MCP_SERVERS`` (JSON, deep-merged)::

    mcp:
      servers:
        agentdeck: {type: http, url: http://host.docker.internal:8765/mcp, headers: {}}

Every server connects at startup; connect failures are **soft** — the server is
marked unavailable and the backend still boots. Agents referencing an
unavailable server boot without its tools rather than crashing.

:meth:`MCPLifecycle.startup` and :meth:`MCPLifecycle.shutdown` are the composition
root's to call (``App`` does, in its lifespan) — resolving tools never connects.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from agentdeck.adapters.tools.mcp.transport import MCPServerStreamableHttpResilient
from agentdeck.errors import ConfigError

# ponytail: the config still comes from process-global v1 settings, which is why this
# adapter reaches into `runtime`. Agents built at import time resolve servers before any
# composition root has run, so there is nobody to pass the config in yet; when `App`
# becomes that root the argument is threaded through and this import goes away.
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.mcp import MCPServer

    from agentdeck.runtime.settings import McpSettings

logger = logging.getLogger(__name__)


def load_mcp_config(settings: McpSettings | None = None) -> dict[str, dict[str, Any]]:
    """Return ``{name: server_spec}`` from the ``mcp:`` settings group, or ``{}``.

    Uses process settings unless an explicit :class:`McpSettings` is passed (tests).
    No servers configured means the backend boots with none — same fail-open rule.
    """
    mcp = settings if settings is not None else get_settings().mcp
    config = mcp.as_config()
    if not config:
        logger.info("No MCP servers configured (mcp.servers empty); none will be connected.")
    return config


def _build_server(name: str, spec: dict[str, Any]) -> MCPServer:
    """Construct an ``MCPServer`` from one JSON entry (http transport only)."""
    transport = (spec.get("type") or "http").lower()
    if transport not in {"http", "streamable-http", "streamable_http"}:
        raise ConfigError(f"MCP server '{name}': unsupported transport '{transport}'")
    url = spec.get("url")
    if not isinstance(url, str):
        raise ConfigError(f"MCP server '{name}': missing `url` for http transport")
    params: dict[str, Any] = {"url": url}
    if isinstance(headers := spec.get("headers"), dict):
        params["headers"] = headers
    if isinstance(timeout := spec.get("timeout"), (int, float)):
        params["timeout"] = timeout
    return MCPServerStreamableHttpResilient(params=params, name=name)


class MCPLifecycle:
    """Process-wide registry of MCP servers, keyed by config name.

    :meth:`startup` connects each configured server (soft per-server failure).
    :meth:`resolve_or_pending` hands an agent its server instance.
    :meth:`shutdown` cleans up. Idempotent; safe to configure/resolve sync.
    """

    _servers: dict[str, MCPServer] = {}
    _failed: dict[str, BaseException] = {}
    _connected: set[str] = set()
    _config: dict[str, dict[str, Any]] = {}
    _lock: asyncio.Lock | None = None

    @classmethod
    def _ensure_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    def configured_names(cls) -> Sequence[str]:
        return tuple(cls._servers.keys())

    @classmethod
    def failed_names(cls) -> Sequence[str]:
        return tuple(cls._failed.keys())

    @classmethod
    def is_available(cls, name: str) -> bool:
        return name in cls._connected

    @classmethod
    def failure_reason(cls, name: str) -> BaseException | None:
        return cls._failed.get(name)

    @classmethod
    def configure(cls, config: dict[str, dict[str, Any]] | None = None) -> None:
        """Load server specs without connecting. Safe to call sync (import-time builds)."""
        if config is None:
            config = load_mcp_config()
        cls._config = config
        for name, spec in config.items():
            if name in cls._servers or name in cls._failed:
                continue
            try:
                cls._servers[name] = _build_server(name, spec)
            except Exception as exc:  # config errors are soft — disable this server, keep the rest
                logger.warning("MCP server '%s' disabled (config error): %r", name, exc)
                cls._failed[name] = exc

    @classmethod
    async def startup(cls, config: dict[str, dict[str, Any]] | None = None) -> None:
        """Configure (if not yet) and connect each known server; failures are soft."""
        cls.configure(config)
        async with cls._ensure_lock():
            for name, server in list(cls._servers.items()):
                if name in cls._connected or name in cls._failed:
                    continue
                logger.info("MCPLifecycle: connecting %s", name)
                try:
                    await server.connect()
                except Exception as exc:  # soft: a down MCP host must never fail the backend's boot
                    logger.error(
                        "MCPLifecycle: connection to '%s' FAILED — agents referencing it "
                        "boot without its tools. Cause: %r",
                        name,
                        exc,
                    )
                    cls._failed[name] = exc
                    # Soft-fail: unreachable for the rest of the process (no boot-time reconnect).
                    # A drop *after* a successful connect is handled by the transport's reconnect.
                    cls._servers.pop(name, None)
                    continue
                cls._connected.add(name)

    @classmethod
    async def shutdown(cls) -> None:
        async with cls._ensure_lock():
            for name in list(cls._connected):
                server = cls._servers.get(name)
                if server is None:
                    cls._connected.discard(name)
                    continue
                try:
                    await server.cleanup()
                except Exception:  # best-effort teardown — keep cleaning up the rest
                    logger.exception("MCPLifecycle: cleanup failed for '%s'", name)
                cls._connected.discard(name)

    @classmethod
    def resolve_or_pending(cls, name: str) -> MCPServer | None:
        """Return the server for ``name`` regardless of connect state, or ``None``.

        Lets agents declared at import time wire up the same instance the lifecycle
        connects later. ``None`` when the name is unknown or its connect failed —
        callers (``BaseAgent._kwargs``) filter that out to boot with reduced capability.
        """
        if name in cls._failed:
            return None
        if name not in cls._servers:
            if not cls._config:
                cls.configure()
            if name not in cls._servers and name not in cls._failed:
                logger.warning(
                    "MCP server '%s' not found in config; agent boots without it. Configured: %r",
                    name,
                    list(cls._servers.keys()),
                )
                return None
        return cls._servers.get(name)

    @classmethod
    def reset(cls) -> None:
        """Tests only — clears state. Does not call cleanup()."""
        cls._servers.clear()
        cls._failed.clear()
        cls._connected.clear()
        cls._config.clear()
        cls._lock = None


__all__ = ["MCPLifecycle", "load_mcp_config"]
