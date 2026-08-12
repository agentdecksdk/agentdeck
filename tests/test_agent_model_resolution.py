"""``Agent(model=...)`` must win for that agent; an agent that declares nothing must still
play on ``OPENAI_MODEL`` exactly as before.

The defect this guards: the SDK's own ``RunConfig.model``, once set, overrides *every*
agent's model regardless of what it declared — so building every run's config with
``model=settings.model`` (``OPENAI_MODEL``, always set) silently discarded any per-agent
``model=``. The fix resolves the default at compile time
(``authoring.compile.compile_agent``) instead, and ``RunConfig.model`` is never set at all —
these tests fail against the old shape and pass against the new one.

No live model anywhere here: every model is a scripted fake, and a call count is what
proves which one actually ran.
"""

from __future__ import annotations

import pytest
from scripted_model import ScriptedModel, patch_provider, provider_of

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents.runconfig import RunSettings
from agentdeck.authoring import Agent
from agentdeck.authoring.compile import compile_agent, link_handoffs
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.runtime.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def _reset_settings_after():
    yield
    reset_settings_cache()


def _ctx() -> RunContext:
    return RunContext(namespace="acme", run_id="r-1", session_id="s-1")


def test_declared_model_compiles_straight_onto_the_sdk_agent():
    declared = ScriptedModel(deltas=["hi"])
    compiled = compile_agent(Agent(name="A", instructions="x", model=declared))
    assert compiled.model is declared


def test_undeclared_model_defaults_to_the_configured_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_MODEL", "the-configured-model")
    reset_settings_cache()

    compiled = compile_agent(Agent(name="A", instructions="x"))

    assert compiled.model == "the-configured-model"


def test_handoff_target_keeps_its_own_declared_model_over_the_router_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_MODEL", "router-default")
    reset_settings_cache()
    specialist = Agent(name="Specialist", instructions="x", model="gpt-specialist")
    router = Agent(name="Router", instructions="x", handoffs=[specialist])

    compiled = {a.name: compile_agent(a) for a in (router, specialist)}
    link_handoffs(compiled, [router, specialist])

    assert compiled["Router"].model == "router-default"
    assert compiled["Specialist"].model == "gpt-specialist"


async def test_a_run_plays_the_declared_model_not_the_configured_one(monkeypatch: pytest.MonkeyPatch):
    """The issue's own repro: a run configured with one model must not reach it when the
    agent playing declared a different one — old code forced every agent onto
    ``RunConfig.model`` (a plain string), so the provider below would have answered instead.
    """
    declared = ScriptedModel(deltas=["from the declared model"])
    provider_model = ScriptedModel(deltas=["from the configured default — wrong"])
    patch_provider(monkeypatch, provider_of(provider_model))

    compiled = compile_agent(Agent(name="A", instructions="x", model=declared))
    spec = InvocableSpec(name="A", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=compiled)
    engine = OpenAIAgentsEngine(ExecutionStore(), settings=RunSettings(model="configured-default"))

    async for _ in engine.start(spec, coerce_input("hi"), [], _ctx()):
        pass

    assert declared.calls == 1
    assert provider_model.calls == 0


async def test_a_run_still_plays_the_configured_model_when_the_agent_declares_none(monkeypatch: pytest.MonkeyPatch):
    """The other half of the contract: dropping ``RunConfig.model`` must not strand an agent
    that names nothing — it still has to reach ``OPENAI_MODEL``, just via the compiled
    agent's own ``model`` field rather than the run config.
    """
    monkeypatch.setenv("OPENAI_MODEL", "configured-default")
    reset_settings_cache()
    configured_model = ScriptedModel(deltas=["from the configured default"])
    patch_provider(monkeypatch, provider_of(configured_model))

    compiled = compile_agent(Agent(name="A", instructions="x"))
    spec = InvocableSpec(name="A", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=compiled)
    # `RunSettings.model` is what turns the provider on at all (`runconfig._provider`) — in
    # the real composition root it is the same `OPENAI_MODEL` value the agent just defaulted
    # to above, so it is named again here rather than left at `RunSettings()`'s bare default.
    engine = OpenAIAgentsEngine(ExecutionStore(), settings=RunSettings(model="configured-default"))

    async for _ in engine.start(spec, coerce_input("hi"), [], _ctx()):
        pass

    assert configured_model.calls == 1
