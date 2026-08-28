"""How one run is configured: plain resolved values in, an SDK ``RunConfig`` out.

The values arrive from the composition root (``agentdeck/composition.py``'s
``resolve_run_settings``) rather than being read here, for the reason the store and the
control port are already resolved there: an adapter that reaches for ``get_settings()``
cannot be handed a different endpoint by a caller, and a second front door would have to
mutate process state to get one.

The adapter maps supported model prefixes to provider endpoints, while bare and unknown
namespaced IDs stay on the configured OpenAI-compatible endpoint. ``RunConfig.model`` remains
unset because the SDK uses it to override every agent's own model; the settings default is
resolved onto undeclared agents at compile time instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx
from agents import ModelSettings, MultiProvider, OpenAIProvider, RunConfig, default_handoff_history_mapper
from agents.models.multi_provider import MultiProviderMap
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from agents.handoffs import HandoffHistoryMapper
    from agents.items import TResponseInputItem


@dataclass(frozen=True, slots=True)
class RunSettings:
    """Everything a run's ``RunConfig`` is resolved from, as values an adapter can hold.

    Defaults are adapter-safe empty values; the composition root supplies project settings.
    """

    model: str | None = None
    api_key: str = ""
    base_url: str = ""
    ca_bundle: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = ""
    openrouter_api_key: str = ""
    use_responses: bool = True
    workflow_name: str = "Agent workflow"
    nest_handoff_history: bool = False
    handoff_ends_on_user_turn: bool = False
    handoff_closing_turn: str = "Please continue."
    temperature: float | None = None
    max_tokens: int | None = None
    max_turns: int = 10


def build_handoff_ends_on_user_turn_mapper(closing_turn: str) -> HandoffHistoryMapper:
    """``RunConfig.handoff_history_mapper``: the SDK's own collapse, plus a closing user turn.

    ``nest_handoff_history`` folds everything into one assistant message, and some
    OpenAI-compatible endpoints reject a request that ends on anything but a user role. The
    collapsed content already carries the real transcript, so ``closing_turn`` (configurable  -
    the default is an English sentence, which isn't right for every deployment) is appended
    rather than repeating it.
    """

    def _mapper(transcript: list[TResponseInputItem]) -> list[TResponseInputItem]:
        closing_item = cast("TResponseInputItem", {"role": "user", "content": closing_turn})
        return [*default_handoff_history_mapper(transcript), closing_item]

    return _mapper


def build_run_config(settings: RunSettings, *, sandbox: Any = None) -> RunConfig:
    """One run's ``RunConfig``.

    Built per run, never once and mutated: ``sandbox`` is this run's workspace handle, and a
    shared config carrying somebody else's would hand two concurrent turns the same session.
    """
    return RunConfig(
        workflow_name=settings.workflow_name,
        # No `model=` here: the SDK's own `RunConfig.model` overrides every agent's model
        # once set, so a per-agent default lives on the compiled SDK agent instead
        # (`authoring.compile.compile_agent`), where an agent's own declaration still wins.
        nest_handoff_history=settings.nest_handoff_history,
        handoff_history_mapper=(
            build_handoff_ends_on_user_turn_mapper(settings.handoff_closing_turn)
            if settings.handoff_ends_on_user_turn
            else None
        ),
        tracing_disabled=not tracing_enabled(),
        model_provider=_build_model_provider(settings),
        # ``include_usage`` asks the Chat-Completions API to emit the streaming usage chunk
        # (prompt/completion tokens)  -  without it, streamed turns carry no token counts at
        # all, so ``usage.reported`` and ``run.completed`` would both report zero. No-op on
        # the Responses API, where usage is always included.
        model_settings=ModelSettings(
            temperature=settings.temperature, max_tokens=settings.max_tokens, include_usage=True
        ),
        sandbox=sandbox,
    )


def tracing_enabled() -> bool:
    """Opt-in switch for the SDK's default trace exporter (issue #61).

    Off by default: a keyless/fake-model run (tests, CI, the M0 demo) has no OpenAI
    account to export traces to, and the SDK's exporter otherwise attempts a real HTTPS
    call on every run, logging a non-fatal ``Tracing client error 401``. Set
    ``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED=true`` to restore it for a deployment that
    wants the SDK's own trace export.

    Not the Langfuse switch it used to be: traces are built from the event stream by
    ``adapters/telemetry/langfuse``, so the SDK's own exporter is a separate question now
    and answered separately.
    """
    raw = os.environ.get("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED")
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_model_provider(settings: RunSettings) -> MultiProvider:
    """Route supported prefixes while preserving bare and namespaced compatible model IDs."""
    providers = MultiProviderMap()
    providers.set_mapping(
        {
            "anthropic": OpenAIProvider(
                base_url="https://api.anthropic.com/v1",
                api_key=settings.anthropic_api_key or None,
                use_responses=False,
            ),
            "gemini": OpenAIProvider(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=settings.gemini_api_key or None,
                use_responses=False,
            ),
            "ollama": OpenAIProvider(
                base_url=settings.ollama_base_url or None,
                api_key="ollama",
                use_responses=False,
            ),
            "openrouter": OpenAIProvider(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key or None,
                use_responses=False,
            ),
        }
    )
    openai_options: dict[str, Any]
    if settings.ca_bundle:
        openai_options = {
            "openai_client": AsyncOpenAI(
                base_url=settings.base_url or None,
                api_key=settings.api_key,
                http_client=httpx.AsyncClient(verify=settings.ca_bundle),
            )
        }
    else:
        openai_options = {
            "openai_api_key": settings.api_key or ("agentdeck" if settings.base_url else None),
            "openai_base_url": settings.base_url or None,
        }
    return MultiProvider(
        provider_map=providers,
        openai_use_responses=settings.use_responses,
        unknown_prefix_mode="model_id",
        **openai_options,
    )


__all__ = [
    "RunSettings",
    "build_handoff_ends_on_user_turn_mapper",
    "build_run_config",
    "tracing_enabled",
]
