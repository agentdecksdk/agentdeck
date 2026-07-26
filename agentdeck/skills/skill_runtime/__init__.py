"""The **skill runtime** — the shared library for LLM-driven skills.

A framework-owned, top-level package (source under ``agentdeck/skills/skill_runtime``)
that a skill script imports as ``skill_runtime`` — exactly as it imports
``agentdecks_core``, and resolved the same way: it is installed in the venv the
sandbox subprocess shares, so no mount or ``PYTHONPATH`` is needed. It has no
framework imports of its own — only ``agentdecks_core``, ``openai`` and (optionally,
for tracing) ``langfuse`` / OpenInference — so a skill never reaches up into ``agentdeck.*``.

Skill-facing surface:

* :func:`call_json` / :class:`LLMSettings` — the one LLM call path; ``call_json``
  resolves a cached client from the settings (one per run, reused), so a skill
  never touches ``AsyncOpenAI``.
* :func:`map_batched` — batch-with-bisect concurrency.
* :func:`capture` — the ``SANDBOX_CAPTURE`` identity reader.

In-sandbox Langfuse tracing rides along automatically: ``call_json`` self-arms it
(a no-op unless the host injected the Langfuse keys) and Langfuse flushes on exit,
so skills never call it — see ``skill_runtime.llm``.
"""

from __future__ import annotations

from .batch import map_batched
from .capture import CAPTURE_ENV, capture
from .llm import (
    DEFAULT_BASE_URL,
    LLMConfigError,
    LLMSettings,
    call_json,
    resolve_max_tokens,
    resolve_temperature,
    strip_fence,
)

__all__ = [
    "CAPTURE_ENV",
    "DEFAULT_BASE_URL",
    "LLMConfigError",
    "LLMSettings",
    "call_json",
    "capture",
    "map_batched",
    "resolve_max_tokens",
    "resolve_temperature",
    "strip_fence",
]
