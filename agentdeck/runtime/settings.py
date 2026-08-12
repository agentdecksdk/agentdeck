"""Layered runtime settings (OpenAI, Runner, ...) backed by env + shared YAML.

A single ``config.yaml`` (resolved via ``AGENTDECK_CONFIG_PATH`` → cwd →
packaged default) hosts every settings group keyed by section: ``openai:``,
``runner:``, ``session:``, ``shell:``. Each :class:`BaseSettings` subclass reads only its section; shell
env vars (prefix-bound, e.g. ``OPENAI_BASE_URL``) override the file. The
project's ``.env`` (found from ``Path.cwd()``, never from this module's own
location) is loaded the first time :func:`get_settings` builds a
:class:`Settings` — not at import — so a ``chdir`` between ``import agentdeck``
and first use still lands on the right project (process env wins either way).

``EVENTS``/``CONTROL``/``CHECKPOINT``/``SESSION`` each read one URL-shaped env var
(``AGENTDECK_EVENTS``, not a ``_BACKEND``/``_URL`` pair) — the scheme names the backend, so
there is no second decision left to disagree with it. See :func:`parse_backend_url`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource

if TYPE_CHECKING:
    from collections.abc import Callable
    from importlib.resources.abc import Traversable

logger = logging.getLogger(__name__)

PACKAGED_DEFAULT_YAML = Path(__file__).resolve().parent / "config.default.yaml"
_CONFIG_PATH_ENV = "AGENTDECK_CONFIG_PATH"


def resolve_env_file() -> Path:
    """The project's ``.env``: ``Path.cwd() / ".env"`` — no upward directory search
    (unlike ``dotenv.find_dotenv()``, which would just as silently load an unrelated
    ancestor's ``.env`` instead of the project's own).

    Resolved fresh by :func:`get_settings` on every call it actually builds, never at
    import time: cwd is what "my project" means for `agentdeck serve`, an installed
    package, and Compose alike, matching how ``mount_project_dir`` locates
    ``./.agentdeck`` (never module-relative, which lands in site-packages for an
    installed package — issue #16); binding it once at import would instead freeze
    whatever cwd happened to be current the moment ``agentdeck`` was first imported,
    which a caller is free to ``chdir`` away from before ever building `Settings`.
    """
    return Path.cwd() / ".env"


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """Resolve the shared YAML: explicit arg → ``AGENTDECK_CONFIG_PATH`` → cwd → packaged default.

    Returning a path that doesn't exist is fine — the YAML source treats a
    missing file as empty, which lets env vars alone drive a fully-defaulted
    config. Resolved from ``Path.cwd()`` on every call, matching
    :func:`resolve_env_file` and how ``App`` locates ``./.agentdeck`` — never
    module-relative (issue #16).
    """
    chosen = explicit or os.environ.get(_CONFIG_PATH_ENV)
    if chosen:
        return Path(str(chosen)).expanduser()
    local = Path.cwd() / "config.yaml"
    return local if local.is_file() else PACKAGED_DEFAULT_YAML


class SectionedYamlSource(YamlConfigSettingsSource):
    """Read a single ``yaml[section]`` mapping so one config.yaml hosts many settings models.

    Permissive on missing files / sections: returns ``{}`` instead of raising,
    so an operator can omit a section entirely and rely on field defaults.
    """

    def __init__(self, settings_cls: type[BaseSettings], section: str | None):
        self._section = section
        super().__init__(settings_cls, yaml_file=resolve_config_path())

    # ``Path | Traversable`` because pydantic-settings widened this parameter and an override
    # may not narrow one (Liskov) — CI, resolving fresh, reads the widened base and rejected the
    # old signature. The dependency is unpinned (`>=2.4`), so both are in the field: the wide
    # annotation is the one that satisfies either base, and the body only needs ``is_file()``,
    # which both types provide.
    def _read_file(self, file_path: Path | Traversable) -> dict[str, Any]:
        if not file_path.is_file():
            return {}
        # ty: ignore[invalid-argument-type] — the same two-version split, seen from the other
        # side: against a `Path`-only base this argument is too wide. It is a `Path` at runtime
        # (the caller is pydantic-settings, resolving our own `yaml_file`), and the widened base
        # accepts both. Drop the ignore once `pydantic-settings` is pinned past the widening.
        data: Any = super()._read_file(file_path) or {}  # ty: ignore[invalid-argument-type]
        if not isinstance(data, Mapping):
            return {}
        if self._section is None:
            return dict(data)
        sub = data.get(self._section, {})
        return dict(sub) if isinstance(sub, Mapping) else {}


def default_use_responses() -> bool:
    """Default to the SDK's Responses transport, overridable via env.

    Set ``OPENAI_USE_RESPONSES=false`` when targeting a Chat-Completions-
    only model server. Default deployments pick up real per-message
    response ids and avoid the ``FAKE_RESPONSES_ID`` collision entirely.
    """
    raw = os.environ.get("OPENAI_USE_RESPONSES")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _yaml_section_for_prefix(prefix: str) -> str:
    """Map an env_prefix to its YAML section name.

    ``OPENAI_`` → ``openai``, ``AGENTDECK_RUNNER_`` → ``runner``,
    ``AGENTDECK_SESSION_`` → ``session``, ``AGENTDECK_SHELL_`` → ``shell``.
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


def parse_backend_url(url: str) -> tuple[str, str]:
    """Split ``scheme://rest`` into ``(scheme, rest)`` — the scheme names the backend
    (``memory``, ``sqlite``, ``redis``, ``postgresql``, …), and ``rest`` is everything after
    it, untouched, so a relative sqlite path round-trips exactly as written (``sqlite://.agentdeck/x.db``
    stays relative; ``sqlite:///var/lib/x.db`` stays absolute).

    A value with no ``://`` at all returns the whole string as ``scheme`` and an empty
    ``rest`` — indistinguishable, on purpose, from any other scheme a caller's own dispatch
    does not recognize, so one "unknown backend" branch covers both.
    """
    scheme, _, rest = url.partition("://")
    return scheme.lower(), rest


def _bare_env_source(names: Mapping[str, str]) -> Callable[[], dict[str, str]]:
    """A settings source reading each ``field -> exact env var name`` in ``names`` verbatim,
    ignoring ``env_prefix``.

    For a decision that is one variable, not ``<PREFIX>_<FIELD>`` — ``AGENTDECK_EVENTS``, never
    ``AGENTDECK_EVENTS_URL`` alongside it. See ``LayeredSettings._bare_env_names``.
    """

    def _read() -> dict[str, str]:
        return {field: value for field, name in names.items() if (value := os.environ.get(name)) is not None}

    return _read


class LayeredSettings(BaseSettings):
    """``BaseSettings`` with two additions: ``with_overrides`` for CLI flag layering
    and a YAML section source keyed off ``env_prefix`` (so one ``config.yaml`` can
    host every subgroup — ``openai:``, ``runner:``, …).

    Used by both runtime settings (``OpenAISettings`` etc.) and backend settings
    (``PolarionSettings`` etc.). One base class, one resolution algorithm.
    """

    _bare_env_names: ClassVar[Mapping[str, str]] = {}
    """Fields read from an exact, unprefixed env var name instead of ``env_prefix + field_name``
    (see :func:`_bare_env_source`). When every field of a model is covered, the normal prefixed
    env source is dropped entirely rather than kept as an undocumented second spelling."""

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
        bare_names = cls._bare_env_names
        if bare_names and set(bare_names) >= set(settings_cls.model_fields):
            sources: tuple[Any, ...] = (init_settings, _bare_env_source(bare_names))
        elif bare_names:
            sources = (init_settings, _bare_env_source(bare_names), env_settings)
        else:
            sources = (init_settings, env_settings)
        return (*sources, SectionedYamlSource(settings_cls, section))


class OpenAISettings(LayeredSettings):
    """OpenAI-compatible endpoint configuration.

    Empty ``base_url`` means the SDK default (api.openai.com); point it at any
    OpenAI-compatible server (vLLM, Ollama, a corporate gateway) to override.
    """

    model_config = settings_config("OPENAI_", protected_namespaces=())
    model: str = Field(description="Model name passed to the host Agents SDK runner. No default — always required.")
    api_key: str = Field(
        default="",
        description="API key for the endpoint. What empty does depends on `ca_bundle`: unset (the common "
        "case), the OpenAI client falls through to its own `OPENAI_API_KEY` process-env lookup and errors on "
        "the first model call if that's empty too; with `ca_bundle` set, the empty value is passed straight "
        "through instead and just sends no Authorization header — the self-hosted/corporate-CA case doesn't "
        "need a placeholder value the way the common path does.",
    )
    base_url: str = Field(
        default="", description="OpenAI-compatible endpoint base URL. Empty uses the SDK default, api.openai.com."
    )
    # Path to a CA/cert bundle used to verify the endpoint's TLS cert. Point it at a
    # corporate CA or a self-signed cert to reach an internal OpenAI-compatible server
    # *without* disabling verification. Empty => system default trust store.
    ca_bundle: str = Field(
        default="",
        description="Path to a CA/certificate bundle for verifying the endpoint's TLS certificate. Empty uses "
        "the system's default trust store.",
    )

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

    workflow_name: str = Field(
        default="agentdeck",
        description="Name recorded on the host Agents SDK run (`RunConfig.workflow_name`) — identifies which "
        "workflow produced a run in tracing/observability.",
    )
    temperature: float = Field(default=1.0, description="Sampling temperature for the host agent loop's model.")
    max_turns: int = Field(
        default=30, description="Maximum turns `Runner.run`/`run_streamed` may take before giving up."
    )
    # Cap on tokens per response for the HOST agent loop (Agents SDK ``ModelSettings``).
    # ``None`` = model default (uncapped).
    max_tokens: int | None = Field(
        default=None,
        description="Cap on tokens per response for the host agent loop's `ModelSettings`. `None` means the "
        "model's own default (uncapped).",
    )


class RuntimeSettings(LayeredSettings):
    """Knobs the Runtime itself reads.

    ``stale_run_after_seconds`` is how long an open run may write nothing before it stops
    holding its session. One session runs one turn at a time, and a run whose process was
    killed outright never records its ending — silence is the only thing that separates it
    from a turn still working, so the session would otherwise stay claimed for good. **One
    hour** by default: generous next to any real turn, short enough that a crash costs a
    session an hour rather than forever, and the trade is deliberate — a permanently wedged
    session is worse than a rare premature takeover. Two consequences worth knowing when
    tuning it: a session a killed process left claimed is refused until it elapses, and a run
    waiting on a human answer for longer than it is closed as failed the next time somebody
    starts a turn on that session.

    **Set it well above the longest stretch a healthy turn can go without writing an event** — a
    slow tool call, a long model call, a human thinking. This is the one setting here that can
    cost you the guarantee rather than tune it: shortened far enough, an open run looks abandoned
    while it is still working, so the next turn takes the session *from a live turn* and both run
    on one conversation. That is not a premature cleanup, it is one turn per session no longer
    holding. The lower bound is a property of the deployment, not of the code — how long a turn
    can be quiet — so it cannot be validated here; positivity is all that is enforced, and at or
    near zero the failure is immediate, since a run's own opening event is already older than the
    cutoff a caller computes a moment later.

    Mind the clock too. Each worker compares *its own* clock against timestamps its peers stamped,
    so across machines the effective window is this value minus the worst skew between them, and a
    worker running more than a window fast takes over live sessions on sight — the same lost
    guarantee, arrived at by skew instead of configuration. Keep the fleet on NTP and treat the
    window as a budget skew eats into.
    """

    model_config = settings_config("AGENTDECK_RUNTIME_")

    stale_run_after_seconds: float = Field(
        default=60.0 * 60.0,
        gt=0,
        description="How long, in seconds, an open run may go without writing an event before it is treated "
        "as abandoned and its session ownership is released for another worker to claim. Must be positive; set "
        "it above the longest gap a healthy turn can go quiet.",
    )

    @property
    def stale_run_after(self) -> timedelta:
        return timedelta(seconds=self.stale_run_after_seconds)


class LangfuseSettings(LayeredSettings):
    """Langfuse LLM-observability export config.

    Namespaced under ``AGENTDECK_LANGFUSE_`` like every other subgroup. The
    Langfuse SDK natively reads the bare ``LANGFUSE_HOST`` / ``LANGFUSE_PUBLIC_KEY``
    / ``LANGFUSE_SECRET_KEY``; we keep config grouped here and pass these in
    explicitly. Tracing stays off unless BOTH keys are present, so a bare
    checkout never ships spans anywhere.
    """

    model_config = settings_config("AGENTDECK_LANGFUSE_")

    public_key: str = Field(
        default="", description="Langfuse public key. Tracing stays off unless this and `secret_key` are both set."
    )
    secret_key: str = Field(
        default="", description="Langfuse secret key. Tracing stays off unless this and `public_key` are both set."
    )
    base_url: str = Field(default="http://localhost:3000", description="Langfuse endpoint.")
    environment: str = Field(default="local", description="Langfuse `environment` tag attached to every exported span.")
    debug: bool = Field(default=False, description="Enable the Langfuse SDK's own debug logging.")
    sample_rate: float = Field(default=1.0, description="Fraction of traces exported to Langfuse, from 0.0 to 1.0.")
    # OTel resource ``service.name`` for every exported span.
    # Without it OpenTelemetry falls back to ``unknown_service``, leaving traces
    # unattributed in the Langfuse UI.
    service_name: str = Field(
        default="agentdeck",
        description="OpenTelemetry resource `service.name` for every exported span. Without it, spans fall back "
        "to `unknown_service` and are unattributed in the Langfuse UI.",
    )

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


class TavilySettings(LayeredSettings):
    """Tavily web-search API. One knob: ``TAVILY_API_KEY`` env var (or YAML ``tavily: api_key:``)."""

    model_config = settings_config("TAVILY_")

    api_key: str = Field(
        default="",
        description="Tavily web-search API key. Empty makes the `web_search` tool return an `error:` string "
        "instead of raising — it degrades the same way an unavailable MCP server does, rather than disappearing.",
    )


class CheckpointSettings(LayeredSettings):
    """LangGraph checkpointer backend for ``durable=True`` workflows, as one scheme-shaped URL.

    ``sqlite://<path>`` for dev (relative or absolute — see :func:`parse_backend_url`;
    ``sqlite://.agentdeck/checkpoints.sqlite3`` is the default), ``postgresql://<dsn>`` for
    prod, ``memory://`` for tests (never persists past the process). Resolving the saver
    classes lives in ``agentdeck.adapters.engines.langgraph.checkpointer`` — sqlite/postgres
    ship in the optional ``[durability]`` extra, so this settings model stays import-free of
    them.
    """

    _bare_env_names: ClassVar[Mapping[str, str]] = {"url": "AGENTDECK_CHECKPOINT"}
    model_config = settings_config("AGENTDECK_CHECKPOINT_")

    url: str = Field(
        default="sqlite://.agentdeck/checkpoints.sqlite3",
        description="LangGraph checkpointer for `durable=True` workflows: `sqlite://<path>` for dev "
        "(this default), `postgresql://<dsn>` for prod, or `memory://` for tests (never persists past the "
        "process). The scheme names the backend.",
    )


class EventsSettings(LayeredSettings):
    """Where the Runtime's canonical event log is written, as one scheme-shaped URL.

    ``memory://`` (the default) keeps it in the process and never touches disk, so a plain
    install needs no configuration and no writable project dir — at the cost of a log that
    grows for as long as the process lives and is gone when it exits. ``sqlite://<path>`` is a
    log that survives a restart.

    ``redis://``/``rediss://`` and ``postgresql://`` (needs the ``[durability]`` extra) are the
    two that several workers can share: SQLite's durability rests on cross-process shared
    memory, so one file behind more than one machine is unsupported. Each keeps to its own
    keyspace, so an instance already holding LangGraph checkpoints or agent conversations is
    fine to reuse. A Redis instance used as the record wants ``appendonly yes`` and
    ``maxmemory-policy noeviction`` — this is a log, not a cache.
    """

    _bare_env_names: ClassVar[Mapping[str, str]] = {"url": "AGENTDECK_EVENTS"}
    model_config = settings_config("AGENTDECK_EVENTS_")

    url: str = Field(
        default="memory://",
        description="Where the Runtime's canonical event log is written: `memory://` (default, in-process, "
        "gone when the process exits), `sqlite://<path>`, `redis://<url>`/`rediss://<url>`, or "
        "`postgresql://<dsn>` (needs the `[durability]` extra). The scheme names the backend.",
    )


class ControlSettings(LayeredSettings):
    """Where a run's pending control signals live — what pause and cancel are written to.

    One scheme-shaped URL. ``memory://`` (the default) keeps them in the process, which is all a
    single worker needs and all it can use: a signal written in one process is invisible to
    another, so with the default backend the ``agentdeck runs signal`` CLI and a second web
    worker cannot reach a run at all. ``sqlite://<path>`` crosses process boundaries — the same
    file the CLI's ``--control-db`` names. SQLite's cross-process story rests on shared memory,
    so one file behind more than one *machine* is unsupported; that one waits for a Redis
    control port.

    This is a tiny table of pending intent, not a log: nothing here is a record of what
    happened to a run — that is the event store's job, and the control events in it.
    """

    _bare_env_names: ClassVar[Mapping[str, str]] = {"url": "AGENTDECK_CONTROL"}
    model_config = settings_config("AGENTDECK_CONTROL_")

    url: str = Field(
        default="memory://",
        description="Where a run's pending control signals live: `memory://` (default, reachable only from "
        "this process) or `sqlite://<path>` (crosses process boundaries — required for the `agentdeck runs "
        "signal` CLI to reach a run). The scheme names the backend.",
    )


class SessionSettings(LayeredSettings):
    """Configuration for Redis-backed agent conversation memory.

    Shared infrastructure: plugins that bridge an external thread/message
    store to ``Runner.run_streamed`` (currently the ChatKit backend) read
    these settings to mint a per-session
    :class:`agents.extensions.memory.RedisSession`. Plugins decide
    whether ``url`` is optional or required — the ChatKit backend
    treats it as required and raises at boot if unset.
    """

    _bare_env_names: ClassVar[Mapping[str, str]] = {"url": "AGENTDECK_SESSION"}
    model_config = settings_config("AGENTDECK_SESSION_")

    url: str | None = Field(
        default=None,
        description="Redis URL for `RedisSession`-backed agent conversation memory "
        "(`agentdeck.adapters.engines.openai_agents.sessions.SessionFactory`). `None` falls back to one "
        "in-process `SQLiteSession` per session key — no persistence across a restart, no sharing across workers.",
    )
    redis_key_prefix: str = Field(
        default="agents:session", description="Key prefix under which `RedisSession` stores conversations in Redis."
    )
    # Per-session TTL in seconds. ``None`` = sessions persist indefinitely.
    redis_ttl: int | None = Field(
        default=None,
        description="Per-session TTL in seconds for Redis-backed conversations. `None` means sessions persist "
        "indefinitely.",
    )


class Settings(BaseModel):
    """Top-level settings aggregating each independently-loaded subgroup."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # default_factory=ClsName goes through env-loaded construction at runtime
    # (pydantic-settings), but pyright sees the class' static signature. Wrap
    # in untyped lambdas so the required-field check on env-backed models
    # doesn't block strict typing.
    openai: OpenAISettings = Field(default_factory=lambda: OpenAISettings.model_validate({}))
    runner: RunnerSettings = Field(default_factory=lambda: RunnerSettings.model_validate({}))
    runtime: RuntimeSettings = Field(default_factory=lambda: RuntimeSettings.model_validate({}))
    checkpoint: CheckpointSettings = Field(default_factory=lambda: CheckpointSettings.model_validate({}))
    events: EventsSettings = Field(default_factory=lambda: EventsSettings.model_validate({}))
    control: ControlSettings = Field(default_factory=lambda: ControlSettings.model_validate({}))
    session: SessionSettings = Field(default_factory=lambda: SessionSettings.model_validate({}))
    langfuse: LangfuseSettings = Field(default_factory=lambda: LangfuseSettings.model_validate({}))
    tavily: TavilySettings = Field(default_factory=lambda: TavilySettings.model_validate({}))


# Names v3 stopped reading, mapped to what replaced them. Nothing binds these any more, so a
# deployment that still exports one would silently fall back to the default — and for the three
# store variables that default is in-process memory, i.e. a durable log quietly becoming
# ephemeral on upgrade. Refusing to start says so instead.
_RETIRED_ENV_NAMES: Mapping[str, str] = {
    "AGENTDECK_EVENTS_BACKEND": "AGENTDECK_EVENTS",
    "AGENTDECK_EVENTS_URL": "AGENTDECK_EVENTS",
    "AGENTDECK_CONTROL_BACKEND": "AGENTDECK_CONTROL",
    "AGENTDECK_CONTROL_URL": "AGENTDECK_CONTROL",
    "AGENTDECK_CHECKPOINT_BACKEND": "AGENTDECK_CHECKPOINT",
    "AGENTDECK_CHECKPOINT_URL": "AGENTDECK_CHECKPOINT",
    "AGENTDECK_SESSION_REDIS_URL": "AGENTDECK_SESSION",
    "AGENTDECK_LANGFUSE_HOST": "AGENTDECK_LANGFUSE_BASE_URL",
    "APP_CONFIG_PATH": "AGENTDECK_CONFIG_PATH",
}


def _refuse_retired_env_names() -> None:
    """A v2-era variable still exported, with nothing set in its place, is a configuration the
    operator believes is in force and is not.

    Only that case: once the replacement is set the migration has happened, and a leftover in an
    inherited container environment should not stop a correctly-configured process from booting.
    """
    found = sorted(
        name
        for name, replacement in _RETIRED_ENV_NAMES.items()
        if os.environ.get(name) and not os.environ.get(replacement)
    )
    if not found:
        return
    lines = "\n  ".join(f"{name} is now {_RETIRED_ENV_NAMES[name]}" for name in found)
    raise ValueError(
        f"these environment variables were replaced in v3 and are no longer read:\n  {lines}\n"
        "They are a single URL now, whose scheme names the backend "
        "(memory:// | sqlite://<path> | redis://... | postgresql://...), so a backend and a URL "
        "can no longer disagree. Unset the old names once you have set the new one."
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Existing process env wins (override=False) so docker-compose / CI exports keep
    # priority over the file; a missing file is a silent no-op.
    load_dotenv(resolve_env_file(), override=False)
    _refuse_retired_env_names()
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = [
    "PACKAGED_DEFAULT_YAML",
    "CheckpointSettings",
    "ControlSettings",
    "EventsSettings",
    "LangfuseSettings",
    "OpenAISettings",
    "RunnerSettings",
    "RuntimeSettings",
    "SectionedYamlSource",
    "SessionSettings",
    "Settings",
    "TavilySettings",
    "default_use_responses",
    "get_settings",
    "parse_backend_url",
    "reset_settings_cache",
    "resolve_config_path",
    "resolve_env_file",
]
