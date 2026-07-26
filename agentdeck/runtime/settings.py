"""Layered runtime settings (OpenAI, Runner, Skills) backed by env + shared YAML.

A single ``config.yaml`` (resolved via ``APP_CONFIG_PATH`` → repo-root →
packaged default) hosts every settings group keyed by section: ``openai:``,
``runner:``, ``session:``, ``shell:``, ``skill:``, ``mcp:``. Each :class:`BaseSettings` subclass reads only its section; shell
env vars (prefix-bound, e.g. ``OPENAI_BASE_URL``) override the file. The
repo-root ``.env`` is loaded once at import (process env wins) so local
``uv run`` invocations see the same overrides Docker Compose injects.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Self

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_DEFAULT_YAML = Path(__file__).resolve().parent / "config.default.yaml"
_CONFIG_PATH_ENV = "APP_CONFIG_PATH"

ENV_FILE = REPO_ROOT / ".env"
# Load repo-root .env once at import. Existing process env wins (override=False)
# so docker-compose / CI exports keep priority over the file.
load_dotenv(ENV_FILE, override=False)

_SKILL_PREFIX = "skill_"


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """Resolve the shared YAML: explicit arg → ``APP_CONFIG_PATH`` → repo-root → packaged default.

    Returning a path that doesn't exist is fine — the YAML source treats a
    missing file as empty, which lets env vars alone drive a fully-defaulted
    config.
    """
    chosen = explicit or os.environ.get(_CONFIG_PATH_ENV)
    if chosen:
        return Path(str(chosen)).expanduser()
    local = REPO_ROOT / "config.yaml"
    return local if local.is_file() else PACKAGED_DEFAULT_YAML


class SectionedYamlSource(YamlConfigSettingsSource):
    """Read a single ``yaml[section]`` mapping so one config.yaml hosts many settings models.

    Permissive on missing files / sections: returns ``{}`` instead of raising,
    so an operator can omit a section entirely and rely on field defaults.
    """

    def __init__(self, settings_cls: type[BaseSettings], section: str | None):
        self._section = section
        super().__init__(settings_cls, yaml_file=resolve_config_path())

    def _read_file(self, file_path: Path) -> dict[str, Any]:
        if not file_path.is_file():
            return {}
        data: Any = super()._read_file(file_path) or {}
        if not isinstance(data, Mapping):
            return {}
        if self._section is None:
            return dict(data)
        sub = data.get(self._section, {})
        return dict(sub) if isinstance(sub, Mapping) else {}


def _yaml_section_for_prefix(prefix: str) -> str:
    """Map an env_prefix to its YAML section name.

    ``OPENAI_`` → ``openai``, ``AGENTDECK_RUNNER_`` → ``runner``,
    ``AGENTDECK_SESSION_`` → ``session``, ``AGENTDECK_SHELL_`` → ``shell``,
    ``SKILL_`` → ``skill``.
    """
    name = prefix.strip().rstrip("_").lower()
    if name.startswith("agentdeck_"):
        name = name[len("agentdeck_") :]
    return name


def settings_config(prefix: str, **overrides: Any) -> SettingsConfigDict:
    """Build a ``model_config`` for any :class:`LayeredSettings` subclass.

    Every subclass binds its env prefix and YAML section through this helper;
    pass ``**overrides`` to extend (``protected_namespaces=()``, ``extra="allow"`` …).
    """
    base: dict[str, Any] = {
        "env_prefix": prefix,
        "case_sensitive": False,
        "extra": "ignore",
    }
    return SettingsConfigDict(**(base | overrides))


def _strip_skill_prefix(key: str) -> str:
    key = key.strip()
    if key.casefold().startswith(_SKILL_PREFIX):
        key = key[len(_SKILL_PREFIX) :]
    return key.lower()


def _as_env_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, separators=(",", ":"))


class LayeredSettings(BaseSettings):
    """``BaseSettings`` with two additions: ``with_overrides`` for CLI flag layering
    and a YAML section source keyed off ``env_prefix`` (so one ``config.yaml`` can
    host every subgroup — ``openai:``, ``runner:``, …).

    Used by both runtime settings (``OpenAISettings`` etc.) and backend settings
    (``PolarionSettings`` etc.). One base class, one resolution algorithm.
    """

    def with_overrides(self, **overrides: Any) -> Self:
        applied = {k: v for k, v in overrides.items() if v is not None}
        return self.model_copy(update=applied) if applied else self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        prefix = settings_cls.model_config.get("env_prefix", "")
        section = _yaml_section_for_prefix(prefix) if prefix else None
        return (init_settings, env_settings, SectionedYamlSource(settings_cls, section))


class OpenAISettings(LayeredSettings):
    """OpenAI-compatible endpoint configuration.

    Empty ``base_url`` means the SDK default (api.openai.com); point it at any
    OpenAI-compatible server (vLLM, Ollama, a corporate gateway) to override.
    """

    model_config = settings_config("OPENAI_", protected_namespaces=())
    model: str
    api_key: str = ""
    base_url: str = ""
    # Legacy OpenAI-native tracing key; only surfaced by the CLI `info` backend now that
    # host tracing runs through Langfuse/OpenInference (see runtime.observability).
    tracing_api_key: str | None = None
    # Path to a CA/cert bundle used to verify the endpoint's TLS cert. Point it at a
    # corporate CA or a self-signed cert to reach an internal OpenAI-compatible server
    # *without* disabling verification. Empty => system default trust store.
    ca_bundle: str = ""

    def env_dict(self) -> dict[str, str]:
        env = {
            "OPENAI_API_KEY": self.api_key,
            "OPENAI_BASE_URL": self.base_url,
            "OPENAI_MODEL": self.model,
            "OPENAI_CA_BUNDLE": self.ca_bundle,
        }
        # Unset values stay unset in the sandbox — an empty OPENAI_BASE_URL would
        # override the OpenAI client's default endpoint resolution.
        return {k: v for k, v in env.items() if v}


class RunnerSettings(LayeredSettings):
    """Defaults for the host-side Agents SDK runner."""

    model_config = settings_config("AGENTDECK_RUNNER_")

    workflow_name: str = "local-sandbox-repl"
    temperature: float = 1.0
    max_turns: int = 30
    # Cap on tokens per response for the HOST agent loop (Agents SDK ``ModelSettings``).
    # Independent of the in-sandbox skill cap ``OPENAI_MAX_TOKENS`` (``skill_runtime``
    # ``resolve_max_tokens``) — the same two-layer split as ``temperature``, not a mirror
    # of it. ``None`` = model default (uncapped).
    max_tokens: int | None = None


class LangfuseSettings(LayeredSettings):
    """Langfuse LLM-observability export config.

    Namespaced under ``AGENTDECK_LANGFUSE_`` like every other subgroup. The
    Langfuse SDK natively reads the bare ``LANGFUSE_HOST`` / ``LANGFUSE_PUBLIC_KEY``
    / ``LANGFUSE_SECRET_KEY``; we keep config grouped here and pass these in
    explicitly. Tracing stays off unless BOTH keys are present, so a bare
    checkout never ships spans anywhere.
    """

    model_config = settings_config("AGENTDECK_LANGFUSE_")

    public_key: str = ""
    secret_key: str = ""
    host: str = "http://localhost:3000"
    environment: str = "local"
    debug: bool = False
    sample_rate: float = 1.0
    # OTel resource ``service.name`` for every exported span (host + sandboxed skills).
    # Without it OpenTelemetry falls back to ``unknown_service``, leaving traces
    # unattributed in the Langfuse UI.
    service_name: str = "agentdeck"

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


class McpServerSettings(BaseModel):
    """One MCP server entry: transport + how to reach it.

    Mirrors a single value in Claude Code's ``mcpServers`` block. Extra keys
    are tolerated so a Claude-Code-shaped spec drops in unchanged. Only the
    HTTP transport is supported today (see ``agentdeck.agents.mcp._build_server``).
    """

    model_config = ConfigDict(extra="allow")

    type: str = "http"
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float | None = None


class McpSettings(LayeredSettings):
    """Named MCP servers an agent can depend on (transport / URL / headers).

    Replaces the old root ``.mcp.json``: servers now live in the shared
    ``config.yaml`` under ``mcp:`` (packaged default in ``config.default.yaml``)
    and override via ``AGENTDECK_MCP_SERVERS`` — a JSON object decoded like every
    other complex env field (cf. ``CHATKIT_CORS_ORIGINS``). pydantic-settings
    deep-merges the map across layers, so env need only restate what changes —
    e.g. ``{"agentdeck":{"url":"http://knowledge-mcp:8765/mcp"}}`` overrides just
    that server's URL and keeps the rest of its YAML spec. Agents reference
    servers by name via ``BaseAgent.mcp_server_names``; this class owns *how* to
    reach each one.
    """

    model_config = settings_config("AGENTDECK_MCP_")

    servers: dict[str, McpServerSettings] = Field(default_factory=dict)

    def as_config(self) -> dict[str, dict[str, Any]]:
        """``{name: spec}`` in the shape :class:`agentdeck.agents.mcp.MCPLifecycle` consumes."""
        return {name: spec.model_dump(exclude_none=True) for name, spec in self.servers.items()}


class TavilySettings(LayeredSettings):
    """Tavily web-search API. One knob: ``TAVILY_API_KEY`` env var (or YAML ``tavily: api_key:``)."""

    model_config = settings_config("TAVILY_")

    api_key: str = ""


class CheckpointSettings(LayeredSettings):
    """LangGraph checkpointer backend for ``durable=True`` workflows.

    ``backend`` picks the saver (``sqlite`` for dev, ``postgres`` for prod,
    ``memory`` for tests — never persists past the process); ``url`` is the
    sqlite file path or the Postgres DSN. Resolving the saver classes lives in
    ``agentdeck.runtime.checkpointer`` — sqlite/postgres ship in the optional
    ``[durability]`` extra, so this settings model stays import-free of them.
    """

    model_config = settings_config("AGENTDECK_CHECKPOINT_")

    backend: str = "sqlite"
    url: str = ""


class SessionSettings(LayeredSettings):
    """Configuration for Redis-backed agent conversation memory.

    Shared infrastructure: plugins that bridge an external thread/message
    store to ``Runner.run_streamed`` (currently the ChatKit backend) read
    these settings to mint a per-session
    :class:`agents.extensions.memory.RedisSession`. Plugins decide
    whether ``redis_url`` is optional or required — the ChatKit backend
    treats it as required and raises at boot if unset.
    """

    model_config = settings_config("AGENTDECK_SESSION_")

    redis_url: str | None = None
    redis_key_prefix: str = "agents:session"
    # Per-session TTL in seconds. ``None`` = sessions persist indefinitely.
    redis_ttl: int | None = None


class SkillsSettings(LayeredSettings):
    """Captures arbitrary ``SKILL_*`` keys (env + YAML ``skill:``); re-exports as ``UPPER_CASE``."""

    model_config = settings_config("SKILL_", extra="allow")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        # BaseSettings only auto-binds env vars matching declared fields
        # after prefix stripping; the inline ``skill_env`` source captures
        # every ``SKILL_*`` key so operators can declare arbitrary names.
        # YAML's ``skill:`` section is the file-side equivalent.

        def skill_env() -> dict[str, str]:
            return {
                _strip_skill_prefix(name): value
                for name, value in os.environ.items()
                if name.casefold().startswith(_SKILL_PREFIX)
            }

        return (
            init_settings,
            env_settings,
            skill_env,
            SectionedYamlSource(settings_cls, "skill"),
        )

    @model_validator(mode="before")
    @classmethod
    def _normalize_input_keys(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        return {_strip_skill_prefix(k) if isinstance(k, str) else k: v for k, v in data.items()}

    def env_dict(self) -> dict[str, str]:
        return {
            name.upper(): rendered for name, value in self.model_dump().items() if (rendered := _as_env_value(value))
        }


class Settings(BaseModel):
    """Top-level settings aggregating each independently-loaded subgroup."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # default_factory=ClsName goes through env-loaded construction at runtime
    # (pydantic-settings), but pyright sees the class' static signature. Wrap
    # in untyped lambdas so the required-field check on env-backed models
    # doesn't block strict typing.
    openai: OpenAISettings = Field(default_factory=lambda: OpenAISettings.model_validate({}))
    runner: RunnerSettings = Field(default_factory=lambda: RunnerSettings.model_validate({}))
    checkpoint: CheckpointSettings = Field(default_factory=lambda: CheckpointSettings.model_validate({}))
    session: SessionSettings = Field(default_factory=lambda: SessionSettings.model_validate({}))
    skills: SkillsSettings = Field(default_factory=lambda: SkillsSettings.model_validate({}))
    langfuse: LangfuseSettings = Field(default_factory=lambda: LangfuseSettings.model_validate({}))
    mcp: McpSettings = Field(default_factory=lambda: McpSettings.model_validate({}))
    tavily: TavilySettings = Field(default_factory=lambda: TavilySettings.model_validate({}))

    def sandbox_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Standard env every sandbox sees: ``OPENAI_*`` + ``SKILL_*`` + extras."""
        return self.openai.env_dict() | self.skills.env_dict() | dict(extra or {})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = [
    "ENV_FILE",
    "PACKAGED_DEFAULT_YAML",
    "REPO_ROOT",
    "CheckpointSettings",
    "LangfuseSettings",
    "McpServerSettings",
    "McpSettings",
    "OpenAISettings",
    "RunnerSettings",
    "SectionedYamlSource",
    "SessionSettings",
    "Settings",
    "SkillsSettings",
    "TavilySettings",
    "get_settings",
    "reset_settings_cache",
    "resolve_config_path",
]
