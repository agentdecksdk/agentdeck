"""``MCP``  -  the capability object over one ``.mcp.json`` file.

Replaces the old ``mcp:`` section of ``config.yaml`` and ``AGENTDECK_MCP_SERVERS``: one file is
now the single source of truth for named MCP servers, in the shape Claude Code already uses  -
an ``mcpServers`` object, keyed by name. ``Agent.mcp`` resolves names against it; this class owns
*how* to reach each one. Construction reads nothing from disk; :meth:`build` parses and
validates the file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentdeck.errors import ConfigError


class McpServerSettings(BaseModel):
    """One MCP server entry: transport + how to reach it.

    Mirrors a single value in Claude Code's ``mcpServers`` block. Extra keys are tolerated so a
    Claude-Code-shaped spec drops in unchanged. Only the HTTP transport is supported today (see
    ``agentdeck.adapters.tools.mcp``).
    """

    model_config = ConfigDict(extra="allow")

    type: str = "http"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float | None = None


class MCP:
    """One ``.mcp.json`` file, holding an ``mcpServers`` object.

    ``build()`` parses the file and validates every entry against :class:`McpServerSettings`;
    it opens no connection, only reads the file. Called again after a change on disk to refresh.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._servers: dict[str, dict[str, Any]] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def build(self) -> dict[str, dict[str, Any]]:
        resolved = self._path.resolve()
        if not resolved.is_file():
            raise ConfigError(f"MCP file not found: {resolved}")
        try:
            data = json.loads(resolved.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{resolved}: invalid JSON  -  {exc}") from exc
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            raise ConfigError(f"{resolved}: expected an object with an 'mcpServers' object.")
        parsed: dict[str, dict[str, Any]] = {}
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                raise ConfigError(f"{resolved}: MCP server {name!r} must be an object, got {type(spec).__name__}.")
            parsed[name] = McpServerSettings.model_validate(spec).model_dump(exclude_none=True)
        self._servers = parsed
        return dict(parsed)

    def config(self) -> dict[str, dict[str, Any]]:
        """``{name: server_spec}``, building on first use."""
        if self._servers is None:
            self.build()
        assert self._servers is not None  # populated by build() on the line above
        return dict(self._servers)

    def names(self) -> frozenset[str]:
        return frozenset(self.config())


__all__ = ["MCP", "McpServerSettings"]
