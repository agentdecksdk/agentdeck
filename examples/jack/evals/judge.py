"""The model that grades. Not the model being graded, wherever that distinction matters.

DeepEval's judged metrics need an LLM. AgentDeck configures exactly one OpenAI-compatible
endpoint per process, so the judge is that endpoint: same ``OPENAI_*`` environment, same client,
no second thing to configure and no second bill.

The judge grading the same model that produced the answer is standard for relevancy and retrieval
and is fine there. It is **not** fine for the adversarial set, where a model that just obeyed an
injection is not a reliable witness to having obeyed it. Those cases assert on the text and use a
judge only as a second opinion. ``AGENTDECK_EVAL_JUDGE_MODEL`` overrides the model when a
stronger or independent grader is available.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _parse(text: str, schema: Any) -> Any:
    """DeepEval asks for a pydantic model back; the endpoint returns prose-wrapped JSON."""
    if schema is None:
        return text
    body = _FENCE.sub("", text).strip()
    if not body.startswith(("{", "[")):
        start = min((i for i in (body.find("{"), body.find("[")) if i != -1), default=-1)
        if start != -1:
            body = body[start:]
    return schema.model_validate(json.loads(body))


class Judge(DeepEvalBaseLLM):
    """The configured OpenAI-compatible endpoint, as a DeepEval judge."""

    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.environ.get("AGENTDECK_EVAL_JUDGE_MODEL") or os.environ["OPENAI_MODEL"]
        base_url = os.environ.get("OPENAI_BASE_URL")
        self._sync = OpenAI(base_url=base_url) if base_url else OpenAI()
        self._async = AsyncOpenAI(base_url=base_url) if base_url else AsyncOpenAI()
        super().__init__(self._model)

    def load_model(self) -> Judge:
        return self

    def get_model_name(self) -> str:
        return f"{self._model} (judge)"

    def _messages(self, prompt: str, schema: Any) -> list[dict[str, str]]:
        if schema is None:
            return [{"role": "user", "content": prompt}]
        # Not `response_format`: it is not carried by every OpenAI-compatible endpoint, and a
        # request that 400s is a worse failure than one that needs its fences stripped.
        return [
            {"role": "system", "content": "Reply with JSON only. No prose, no code fence."},
            {"role": "user", "content": prompt},
        ]

    def generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        reply = self._sync.chat.completions.create(
            model=self._model, messages=self._messages(prompt, schema), temperature=0
        )
        return _parse(reply.choices[0].message.content or "", schema)

    async def a_generate(self, prompt: str, schema: Any = None, **_: Any) -> Any:
        reply = await self._async.chat.completions.create(
            model=self._model, messages=self._messages(prompt, schema), temperature=0
        )
        return _parse(reply.choices[0].message.content or "", schema)
