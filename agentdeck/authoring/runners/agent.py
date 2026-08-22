"""Direct-call agent runner: agent + resolved ``RunConfig``, no sandbox, no event log.

Used by :class:`~agentdeck.authoring.nodes.AgentNode` (a nested agent turn inside a workflow
node)  -  a Runtime-driven turn never touches this; it goes through
``adapters/executors/openai_agents/engine.py`` instead. Sandbox attachment (v1's
``BaseRunner.attach_sandbox``) is gone with ``BaseSandboxAgent``: no agent compiled through
``authoring`` needs one in v3 (sandboxing is disabled, tracked in #163).

This runner opens no spans of its own. Tracing is a Deck-level capability
(``agentdeck.observability.Langfuse``) rendered from the canonical event stream by the
telemetry sink; a runner that also opened a root observation is what produced the second,
sessionless trace tree #162 reported.
"""

from __future__ import annotations

from contextlib import aclosing
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, cast

from agents import Agent, RunConfig, Runner, RunResult

from agentdeck.adapters.executors.openai_agents.runconfig import RunSettings, build_run_config
from agentdeck.runtime.settings import Settings, default_use_responses, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator


@dataclass(frozen=True, slots=True)
class StreamDone:
    """Terminal sentinel yielded by :meth:`HeadlessRunner.run_streamed` after the last delta.

    Carries what a streamed turn would otherwise lose: the SDK's own ``final_output``
    (validated model for an ``output_type`` agent, last assistant message otherwise  -
    not the re-joined deltas, which disagree for tool-using agents) and the turn's
    token usage.
    """

    final_output: Any = None
    usage: dict[str, int] = field(default_factory=dict)


def _usage_of(result: Any) -> dict[str, int]:
    """Flatten the SDK's ``Usage`` into a JSON-safe dict (empty when unavailable)."""
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return {}
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


@dataclass(slots=True)
class BaseRunner:
    """Holds an agent and a resolved :class:`RunConfig`."""

    agent: Agent
    run_config: RunConfig
    max_turns: int = field(default_factory=lambda: get_settings().runner.max_turns)

    @classmethod
    def from_agent(
        cls,
        agent: Agent,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        workflow_name: str | None = None,
        temperature: float | None = None,
        max_turns: int | None = None,
        max_tokens: int | None = None,
        **runner_options: Any,
    ) -> Self:
        """Build a runner with run config resolved from settings + per-call kwargs."""
        settings: Settings = get_settings()
        # `model` is not passed to `with_overrides` here: `openai.model` no longer feeds
        # `RunConfig.model` below, so overriding it on this object would resolve a value
        # nothing reads.
        openai = settings.openai.with_overrides(api_key=api_key, base_url=base_url)
        runner = settings.runner.with_overrides(
            workflow_name=workflow_name,
            temperature=temperature,
            max_turns=max_turns,
            max_tokens=max_tokens,
        )
        run_config = build_run_config(
            RunSettings(
                model=openai.model,
                api_key=openai.api_key,
                base_url=openai.base_url,
                ca_bundle=openai.ca_bundle,
                anthropic_api_key=settings.providers.anthropic_api_key,
                gemini_api_key=settings.providers.gemini_api_key,
                ollama_base_url=settings.providers.ollama_base_url,
                openrouter_api_key=settings.providers.openrouter_api_key,
                use_responses=default_use_responses(),
                workflow_name=runner.workflow_name,
                nest_handoff_history=True,
                handoff_ends_on_user_turn=runner.handoff_ends_on_user_turn,
                handoff_closing_turn=runner.handoff_closing_turn,
                temperature=runner.temperature,
                max_tokens=runner.max_tokens,
                max_turns=runner.max_turns,
            )
        )
        # Only an explicit per-call override belongs on RunConfig: it overrides the agent's
        # own model, while OPENAI_MODEL was already resolved when the agent compiled.
        run_config.model = model
        return cls(
            agent=agent,
            run_config=run_config,
            max_turns=runner.max_turns,
            **runner_options,
        )

    async def run(self) -> Any:
        """Drive the configured agent."""
        raise NotImplementedError

    def run_streamed(self, message: Any = None, *, session: Any = None) -> AsyncIterator[Any]:
        """Streamed counterpart to :meth:`run`, yielding incremental output.

        Not abstract: streaming depends on the underlying engine, so a runner opts in by
        overriding this as an async generator (see :class:`HeadlessRunner`).
        """
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")


@dataclass(slots=True)
class HeadlessRunner(BaseRunner):
    """Single-invocation runner: no sandbox, no event log."""

    async def run(self, message: Any = None, *, session: Any = None) -> RunResult:
        return await Runner.run(
            self.agent,
            message,
            run_config=self.run_config,
            max_turns=self.max_turns,
            session=session,
        )

    async def run_streamed(self, message: Any = None, *, session: Any = None) -> AsyncIterator[str | StreamDone]:
        """Async-generator counterpart to :meth:`run`: yields text deltas, then one :class:`StreamDone`."""
        result = Runner.run_streamed(
            self.agent,
            message,
            run_config=self.run_config,
            max_turns=self.max_turns,
            session=session,
        )
        stream = cast("AsyncGenerator[Any, None]", result.stream_events())
        try:
            async with aclosing(stream) as events:
                async for event in events:
                    if event.type == "raw_response_event" and event.data.type == "response.output_text.delta":
                        yield event.data.delta
        finally:
            result.cancel()
        yield StreamDone(final_output=result.final_output, usage=_usage_of(result))


__all__ = ["BaseRunner", "HeadlessRunner", "StreamDone"]
