"""Compaction capability spec — picks static vs. dynamic policy from a threshold."""

from __future__ import annotations

from typing import Any

from agents.sandbox.capabilities import Compaction
from agents.sandbox.capabilities.compaction import (
    CompactionModelInfo,
    CompactionPolicy,
    DynamicCompactionPolicy,
    StaticCompactionPolicy,
)
from pydantic import BaseModel, ConfigDict, field_validator


class _ChatCompletionsCompaction(Compaction):
    """Compaction without Responses-API sampling params.

    Base ``Compaction.sampling_params`` injects ``context_management``
    into ``extra_args``, which chat-completions models forward on the
    wire and the server rejects. Returning ``{}`` keeps the capability
    registered (instructions, ``process_context``) but inert.
    """

    def sampling_params(self, sampling_params: dict[str, Any]) -> dict[str, Any]:
        del sampling_params
        return {}


class CompactionSpec(BaseModel):
    """Compaction policy spec.

    ``threshold``: ``None`` → SDK runtime default; ``int > 1`` →
    :class:`StaticCompactionPolicy`; ``float in (0, 1]`` →
    :class:`DynamicCompactionPolicy` at that fraction of the model's
    context window (requires the model to be in
    :class:`CompactionModelInfo`'s table).
    """

    model_config = ConfigDict(extra="forbid")

    threshold: int | float | None = None
    model: str | None = None

    @field_validator("threshold", mode="before")
    @classmethod
    def _validate_threshold(cls, value: object) -> object:
        # isinstance(True, int) is True — reject bool explicitly.
        if isinstance(value, bool):
            raise ValueError("compaction.threshold must be an int or float, not bool")
        if isinstance(value, float) and not 0 < value <= 1:
            raise ValueError(f"compaction.threshold as a fraction must be in (0, 1]; got {value!r}")
        if isinstance(value, int) and value <= 1:
            raise ValueError(f"compaction.threshold as an absolute token cap must be > 1; got {value!r}")
        return value

    def build(self, *, agent_model: str | None = None) -> Compaction:
        policy = self._resolve_policy(agent_model)
        return _ChatCompletionsCompaction(policy=policy) if policy else _ChatCompletionsCompaction()

    def _resolve_policy(self, agent_model: str | None) -> CompactionPolicy | None:
        if self.threshold is None:
            return None
        if isinstance(self.threshold, float):
            model = self.model or agent_model
            if not model:
                raise ValueError(
                    "compaction.threshold as a fraction requires a model — "
                    "set compaction.model or the agent's `model` attribute.",
                )
            info = CompactionModelInfo.maybe_for_model(model)
            if info is None:
                raise ValueError(
                    f"compaction.threshold as a fraction requires a model with a known "
                    f"context window; '{model}' is not in CompactionModelInfo's table. "
                    "Use an int threshold for an absolute token cap instead.",
                )
            return DynamicCompactionPolicy(model_info=info, threshold=self.threshold)
        return StaticCompactionPolicy(threshold=self.threshold)


__all__ = ["CompactionSpec"]
