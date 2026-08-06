"""What the openai-agents engine hands the SDK, and what it reports back.

Issue #61: it must not enable the SDK's default trace exporter on a keyless/fake-model run
unless explicitly opted in via ``AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED``. Issue #101: an
``output_type`` agent's validated result travels as a ``DataBlock`` on ``run.completed``
instead of failing the run.

Patches ``Runner.run_streamed`` itself (rather than driving a real fake model through a
full run) so the assertion is exactly on what the engine hands the SDK and what it makes of
the result, regardless of how the stream plays out.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from agents import Agent, RunConfig
from agents.models.interface import Model
from pydantic import BaseModel

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents import engine as engine_module
from agentdeck.core.content import DataBlock, TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.events import RunCompleted, Usage
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

    def __init__(self, final_output: Any = "ok") -> None:
        self.final_output = final_output
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


# --- the final output (issue #101) ------------------------------------------------------


class _Slot(BaseModel):
    day: str
    hour: int


@dataclasses.dataclass
class _Decision:
    approved: bool


async def _terminal(monkeypatch: pytest.MonkeyPatch, final_output: Any) -> RunCompleted:
    class _FakeRunner:
        @staticmethod
        def run_streamed(*_args: Any, **_kwargs: Any) -> _FakeStreamResult:
            return _FakeStreamResult(final_output)

    monkeypatch.setattr(engine_module, "Runner", _FakeRunner)
    engine = OpenAIAgentsEngine(ExecutionStore())
    payloads = [payload async for payload in engine.start(_spec(), coerce_input("hi"), [], _ctx())]
    terminal = payloads[-1]
    assert isinstance(terminal, RunCompleted)
    return terminal


async def test_a_text_final_output_is_a_text_block(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = await _terminal(monkeypatch, "ok")
    assert terminal.output == [TextBlock(text="ok")]


async def test_a_validated_output_type_result_is_a_data_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """It used to raise, which turned a documented feature into a failed run."""
    terminal = await _terminal(monkeypatch, _Slot(day="tuesday", hour=9))
    assert terminal == RunCompleted(
        output=[DataBlock(data={"day": "tuesday", "hour": 9})],
        usage=Usage(input_tokens=0, output_tokens=0),
    )


async def test_a_dataclass_output_type_result_is_a_data_block(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = await _terminal(monkeypatch, _Decision(approved=True))
    assert terminal.output == [DataBlock(data={"approved": True})]


async def test_an_output_the_sdk_cannot_json_becomes_its_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """The declared ceiling: a run reports its answer rather than failing at its last event."""
    terminal = await _terminal(monkeypatch, _NeverCalledModel)
    assert terminal.output == [DataBlock(data=str(_NeverCalledModel))]
