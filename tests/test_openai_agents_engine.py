"""Issue #61: the openai-agents engine must not enable the SDK's default trace exporter
on a keyless/fake-model run unless explicitly opted in via
``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED``.

Patches ``Runner.run_streamed`` itself (rather than driving a real fake model through a
full run) so the assertion is exactly on what the engine hands the SDK, regardless of how
the stream plays out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agents import Agent, RunConfig
from agents.models.interface import Model

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents import engine as engine_module
from agentdeck.core.content import coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.invocable import InvocableKind, InvocableSpec

if TYPE_CHECKING:
    import pytest


class _NeverCalledModel(Model):
    """The fake ``Runner.run_streamed`` below never touches the model; this just satisfies
    ``Agent``'s constructor."""

    async def stream_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError
        yield  # pragma: no cover — makes this an async generator; never reached

    async def get_response(self, *_a: Any, **_k: Any) -> Any:
        raise NotImplementedError


class _FakeStreamResult:
    """Stands in for ``RunResultStreaming``: an immediately-empty event stream."""

    def __init__(self) -> None:
        self.final_output = "ok"
        self.context_wrapper = type("ContextWrapper", (), {"usage": None})()

    async def stream_events(self) -> Any:
        return
        yield  # pragma: no cover — makes this an async generator; never reached

    def cancel(self) -> None:
        pass


def _spec() -> InvocableSpec:
    agent = Agent(name="Test", instructions="reply", model=_NeverCalledModel())
    return InvocableSpec(name="Test", kind=InvocableKind.AGENT, engine=OpenAIAgentsEngine.engine, native=agent)


def _ctx() -> RunContext:
    return RunContext(tenant="acme", principal="user:1", run_id="r-1", trace_id="tr-1", session_id="s-1")


async def _run_config_passed_to_runner(monkeypatch: pytest.MonkeyPatch) -> RunConfig:
    captured: dict[str, Any] = {}

    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, run_config: RunConfig | None = None, **_kwargs: Any) -> _FakeStreamResult:
            captured["run_config"] = run_config
            return _FakeStreamResult()

    monkeypatch.setattr(engine_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())
    async for _ in engine.start(_spec(), coerce_input("hi"), [], _ctx()):
        pass
    return captured["run_config"]


async def test_start_disables_tracing_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", raising=False)
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config is not None
    assert run_config.tracing_disabled is True


async def test_start_keeps_tracing_off_for_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "false")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is True


async def test_start_enables_tracing_on_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED", "true")
    run_config = await _run_config_passed_to_runner(monkeypatch)
    assert run_config.tracing_disabled is False
