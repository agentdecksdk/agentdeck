"""``agentdeck.mcp.MCP``: one ``.mcp.json`` file, parsed and validated at ``build()``  -
the replacement for the old ``mcp:`` section of ``config.yaml`` and ``AGENTDECK_MCP_SERVERS``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from agentdeck.errors import ConfigError
from agentdeck.mcp import MCP

if TYPE_CHECKING:
    from pathlib import Path


def _write_mcp_json(path: Path, servers: dict) -> Path:
    mcp_json = path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": servers}))
    return mcp_json


def test_parses_the_mcp_servers_object(tmp_path):
    path = _write_mcp_json(tmp_path, {"docs": {"type": "http", "url": "http://host/mcp"}})

    config = MCP(path).config()

    assert config == {"docs": {"type": "http", "url": "http://host/mcp", "headers": {}}}


def test_config_builds_lazily_and_caches_until_asked_to_refresh(tmp_path):
    path = _write_mcp_json(tmp_path, {"docs": {"url": "http://a"}})
    mcp = MCP(path)

    first = mcp.config()
    path.write_text(json.dumps({"mcpServers": {"docs": {"url": "http://b"}}}))
    cached = mcp.config()
    refreshed = mcp.build()

    assert first["docs"]["url"] == cached["docs"]["url"] == "http://a"
    assert refreshed["docs"]["url"] == "http://b"


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        MCP(tmp_path / "does-not-exist.mcp.json").build()


def test_invalid_json_is_a_config_error(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{not json")

    with pytest.raises(ConfigError, match="invalid JSON"):
        MCP(path).build()


def test_missing_mcp_servers_key_is_a_config_error(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"servers": {}}))

    with pytest.raises(ConfigError, match="mcpServers"):
        MCP(path).build()


def test_a_server_entry_that_is_not_an_object_is_a_config_error(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"docs": "http://host/mcp"}}))

    with pytest.raises(ConfigError, match="docs"):
        MCP(path).build()


def test_extra_keys_in_a_server_entry_are_tolerated(tmp_path):
    path = _write_mcp_json(tmp_path, {"docs": {"url": "http://host", "future_field": "x"}})

    config = MCP(path).config()

    assert config["docs"]["future_field"] == "x"


def test_names_lists_every_configured_server(tmp_path):
    path = _write_mcp_json(tmp_path, {"docs": {"url": "http://a"}, "crm": {"url": "http://b"}})

    assert MCP(path).names() == {"docs", "crm"}
