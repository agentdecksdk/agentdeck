"""Multi-provider routing, with no model calls.

Issue #519: ``Deck.build()`` used to reject a model for a missing provider env var, which was
neither AgentDeck's to own (the wrapped Agents SDK and the provider resolve auth) nor authoritative
(a bad key value passed the old check; a validly-configured client with no env var failed it). It
no longer checks credentials at all: every prefix below builds with nothing set.
"""

from __future__ import annotations

import pytest
from agents import MultiProvider

from agentdeck import Agent, Deck
from agentdeck.adapters.executors.openai_agents.runconfig import RunSettings, build_run_config
from agentdeck.runtime.settings import reset_settings_cache


def _resolved(model: str, **settings: str):
    provider = build_run_config(RunSettings(model=model, **settings)).model_provider
    assert isinstance(provider, MultiProvider)
    return provider.get_model(model)


@pytest.mark.parametrize(
    ("model", "settings", "resolved_name", "base_url", "api_key"),
    [
        ("openai/gpt-4o", {"api_key": "openai-key"}, "gpt-4o", "https://api.openai.com/v1/", "openai-key"),
        (
            "anthropic/claude-3-7-sonnet",
            {"anthropic_api_key": "anthropic-key"},
            "claude-3-7-sonnet",
            "https://api.anthropic.com/v1/",
            "anthropic-key",
        ),
        (
            "gemini/gemini-2.5-flash",
            {"gemini_api_key": "gemini-key"},
            "gemini-2.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "gemini-key",
        ),
        (
            "ollama/llama3.2",
            {"ollama_base_url": "http://ollama:11434/v1"},
            "llama3.2",
            "http://ollama:11434/v1/",
            "ollama",
        ),
        (
            "openrouter/openai/gpt-4o",
            {"openrouter_api_key": "openrouter-key"},
            "openai/gpt-4o",
            "https://openrouter.ai/api/v1/",
            "openrouter-key",
        ),
    ],
)
def test_prefixed_model_routes_to_its_provider(
    model: str,
    settings: dict[str, str],
    resolved_name: str,
    base_url: str,
    api_key: str,
) -> None:
    resolved = _resolved(model, **settings)

    assert resolved.model == resolved_name
    assert str(resolved._client.base_url) == base_url
    assert resolved._client.api_key == api_key


def test_namespaced_model_remains_supported_by_an_openai_compatible_endpoint() -> None:
    resolved = _resolved(
        "vendor/model-name",
        api_key="gateway-key",
        base_url="https://gateway.invalid/v1",
    )

    assert resolved.model == "vendor/model-name"
    assert str(resolved._client.base_url) == "https://gateway.invalid/v1/"


def test_keyless_openai_compatible_endpoint_gets_an_internal_client_placeholder() -> None:
    resolved = _resolved("local/model", base_url="http://models.invalid/v1")

    assert resolved._client.api_key == "agentdeck"


@pytest.mark.parametrize(
    ("model", "env_vars"),
    [
        ("anthropic/claude-3-7-sonnet", ("ANTHROPIC_API_KEY",)),
        ("gemini/gemini-2.5-flash", ("GEMINI_API_KEY",)),
        ("ollama/llama3.2", ("OLLAMA_BASE_URL",)),
        ("openrouter/openai/gpt-4o", ("OPENROUTER_API_KEY",)),
        ("litellm/vertex_ai/gemini-1.5-pro", ("OPENAI_API_KEY", "OPENAI_BASE_URL")),
        ("any-llm/anthropic/claude-3-7-sonnet", ("OPENAI_API_KEY", "OPENAI_BASE_URL")),
        (None, ("OPENAI_API_KEY", "OPENAI_BASE_URL")),  # the undeclared default
    ],
)
def test_build_succeeds_with_no_provider_credential_set(
    monkeypatch: pytest.MonkeyPatch, model: str | None, env_vars: tuple[str, ...]
) -> None:
    for name in env_vars:
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()

    Deck(agents=[Agent(name="Test", model=model)]).build()


def test_build_accepts_non_string_sdk_models_without_provider_credentials() -> None:
    from agentdeck.testing import ScriptedModel

    Deck(agents=[Agent(name="Scripted", model=ScriptedModel(deltas=["done"]))]).build()
