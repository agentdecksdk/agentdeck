"""The one OpenAI chat-completion path every LLM-driven skill shares.

Consolidates what used to be copy-pasted into each skill's ``_llm.py`` /
``classify.py`` / ``_translate.py``: env-driven settings, the TLS-insecure client
build, ```` ```json ```` fence-stripping, and the single retry loop with the
``response_format=json_object`` fallback.

A skill supplies only the task-specific parts: the ``system``/``user`` prompts and
a ``parse`` callback that turns the model's text into the skill's own schema. The
``parse`` callback signals a *retryable* bad response by raising ``ValueError``
(``json.JSONDecodeError`` included) or pydantic ``ValidationError``.

**In-sandbox tracing** rides along here: :meth:`LLMSettings.from_env` calls
:func:`install_tracing` once, arming Langfuse in the parent context *before* a batched
skill fans its concurrent LLM calls out — so every call inherits the injected trace
context, not just the first. The host injects the standard ``LANGFUSE_*`` env (plus W3C
``TRACEPARENT``/``BAGGAGE`` carriers) into the sandbox; here :func:`langfuse.get_client`
reads it and stands up the OTLP export — Langfuse owns the endpoint, auth and flush —
while OpenInference instruments ``AsyncOpenAI``, so there is no per-call span code. A
no-op unless the host set the Langfuse keys.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any, TypeVar

import httpx
from openai import APIError, AsyncOpenAI

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# NOTE: input is pre-stripped and the captured group is stripped again, so the
# fence regex carries no surrounding ``\s*`` (its overlap with ``.*?`` under
# DOTALL is the backtracking risk S5852 flags).
_FENCE_RE = re.compile(r"^```(?:json)?(.*?)```$", re.DOTALL | re.IGNORECASE)

T = TypeVar("T")


class LLMConfigError(RuntimeError):
    """Required OpenAI configuration (API key / model) is missing from the env."""


@dataclass(frozen=True, slots=True)
class LLMSettings:
    """Connection + sampling settings, resolved from the ``OPENAI_*`` env the host
    injects into the sandbox."""

    model: str
    base_url: str
    api_key: str
    ca_bundle: str | None
    retries: int
    timeout: float
    temperature: float
    max_tokens: int | None = None  # cap on tokens per response; None => model default (uncapped)
    use_response_format: bool = True  # some OpenAI-compatible servers reject it; call_json drops it on 400

    @classmethod
    def from_env(
        cls,
        *,
        retries: int,
        timeout: float,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMSettings:
        """Read ``OPENAI_API_KEY``/``OPENAI_MODEL`` (required) + ``OPENAI_BASE_URL``/
        ``OPENAI_CA_BUNDLE``/``OPENAI_TEMPERATURE``/``OPENAI_MAX_TOKENS`` (optional).

        Arms in-sandbox tracing here — the single entry every LLM skill calls once, in the
        parent context before any :func:`skill_runtime.map_batched` fan-out — so all of a
        batch's concurrent LLM calls inherit the injected trace context, not only the first.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL")
        if not api_key or not model:
            raise LLMConfigError("OPENAI_API_KEY and OPENAI_MODEL must be set.")
        install_tracing()
        return cls(
            model=model,
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL),
            api_key=api_key,
            ca_bundle=os.getenv("OPENAI_CA_BUNDLE") or None,
            retries=max(1, retries),
            timeout=float(timeout),
            temperature=resolve_temperature(temperature),
            max_tokens=resolve_max_tokens(max_tokens),
        )


def resolve_temperature(cli_value: float | None) -> float:
    """Sampling temperature: explicit value wins, else ``OPENAI_TEMPERATURE``, else 0.0.

    Defaults to 0.0 — every LLM call in these skills is a deterministic-judgment
    task (classify / synthesize / audit / translate), where reproducibility beats
    diversity.
    """
    if cli_value is not None:
        return cli_value
    env = os.getenv("OPENAI_TEMPERATURE")
    return float(env) if env else 0.0


def resolve_max_tokens(cli_value: int | None) -> int | None:
    """Max tokens per response: explicit value wins, else ``OPENAI_MAX_TOKENS``, else ``None``.

    ``None`` leaves the response uncapped (the model's own default). This is the
    in-sandbox skill cap; the host agent loop has its own separate cap
    (``RunnerSettings.max_tokens``) — the same two-layer split as temperature.
    """
    if cli_value is not None:
        return cli_value
    env = os.getenv("OPENAI_MAX_TOKENS")
    return int(env) if env else None


def strip_fence(content: str) -> str:
    """Strip a leading/trailing ```` ```json ```` fence, if the model wrapped its JSON."""
    text = content.strip()
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text


@cache
def _client_for(settings: LLMSettings) -> AsyncOpenAI:
    """The one ``AsyncOpenAI`` per unique ``settings``, reused across every call.

    A run's settings never change, so every :func:`call_json` shares a single
    client and its connection pool instead of building one per request — the
    framework owns this, skills stay client-unaware. Cached on ``settings`` (a
    frozen dataclass, hence a valid key); when ``ca_bundle`` is set it verifies
    TLS against that CA/cert bundle (for internal / self-signed endpoints).
    Tracing is armed earlier, in :meth:`LLMSettings.from_env`.
    """
    http_client = httpx.AsyncClient(verify=settings.ca_bundle) if settings.ca_bundle else None
    return AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url, http_client=http_client)


def reset_client_cache() -> None:
    """Drop the cached clients. For tests that swap ``AsyncOpenAI`` or vary settings."""
    _client_for.cache_clear()


async def call_json(
    *,
    system: str,
    user: str,
    settings: LLMSettings,
    parse: Callable[[str], T],
    stage: str,
) -> T:
    """One chat completion → ``parse(fence-stripped text)``, retried on transient errors.

    On a 400 from ``response_format=json_object`` the constraint is dropped and
    retries continue — some OpenAI-compatible servers reject it. A ``parse`` that
    raises ``ValueError``/``ValidationError`` is treated as a retryable bad
    response. Raises ``RuntimeError`` naming ``stage`` when retries are exhausted.

    The client is resolved from ``settings`` here (cached + reused) — callers pass
    only prompts + parse.
    """
    client = _client_for(settings)
    use_response_format = settings.use_response_format
    send_metadata = True
    last_err: Exception | None = None
    for attempt in range(settings.retries):
        try:
            kwargs: dict[str, Any] = {
                "model": settings.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "timeout": settings.timeout,
                "temperature": settings.temperature,
            }
            if settings.max_tokens is not None:
                kwargs["max_tokens"] = settings.max_tokens
            if send_metadata:
                # Tags the generation span with which stage/attempt it is. OpenAI requires
                # string metadata values, so the attempt count is stringified.
                kwargs["metadata"] = {"stage": stage, "attempt": str(attempt + 1)}
            if use_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            response = await client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            return parse(strip_fence(content))
        # APIError covers every transient API failure (connection/timeout/rate-limit/
        # status are all subclasses); a ValueError — pydantic ValidationError is a
        # subclass — is a bad response the parse callback rejected. Both retry; a 400
        # drops the optional params (response_format=json_object, metadata) some
        # OpenAI-compatible servers reject, so a strict server can't loop to exhaustion.
        except (APIError, ValueError) as exc:
            last_err = exc
            if getattr(exc, "status_code", None) == 400:
                use_response_format = send_metadata = False
            await asyncio.sleep(min(2**attempt, 8))
    raise RuntimeError(f"{stage}: exhausted {settings.retries} retries; last error: {last_err!r}")


# --- in-sandbox tracing ------------------------------------------------------
# The host injects the standard LANGFUSE_* env (+ W3C TRACEPARENT/BAGGAGE) into the sandbox;
# get_client() reads it and owns the OTLP export, so there is nothing to hand-wire
# here. LANGFUSE_PUBLIC_KEY gates activation.
_traced = False


def install_tracing() -> None:
    """Route this skill's OpenAI calls into Langfuse once, iff the host injected keys.

    :meth:`LLMSettings.from_env` calls this once, in the parent context before a batched
    skill fans its concurrent LLM calls out, so the adopted trace context reaches every
    call. ``get_client()`` reads the injected ``LANGFUSE_*`` env and stands up the OTLP
    exporter to the Langfuse host (registering the global ``TracerProvider`` and its own
    ``atexit`` flush); OpenInference then instruments ``AsyncOpenAI`` onto it. Adopting the
    injected ``TRACEPARENT`` nests every skill span under the orchestrator's trace, and
    adopting ``BAGGAGE`` carries Langfuse trace attributes over the process boundary.
    Idempotent and fail-safe: any setup error is swallowed — tracing must never break a
    skill.
    """
    global _traced
    if _traced or not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return
    try:
        from langfuse import get_client
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from opentelemetry import context
        from opentelemetry.propagate import extract

        # A down Langfuse must not slow this short-lived subprocess or spam its stderr: the
        # OTLP exporter retries a failed batch on flush-on-exit, logging a WARNING each try
        # (the host bounds the timeout via the injected OTEL_EXPORTER_OTLP_TRACES_TIMEOUT).
        logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(logging.ERROR)
        get_client()  # reads LANGFUSE_* → registers the global TracerProvider + OTLP export
        carrier: dict[str, str] = {}
        if traceparent := os.getenv("TRACEPARENT"):
            carrier["traceparent"] = traceparent
        if baggage := os.getenv("BAGGAGE"):
            carrier["baggage"] = baggage
        if carrier:
            # Adopt the orchestrator's span (trace-context) + its Langfuse attrs (baggage)
            # via the global W3C propagator. Not detached: the subprocess is short-lived,
            # and tasks scheduled after this inherit the context.
            context.attach(extract(carrier))
        OpenAIInstrumentor().instrument()
        _traced = True
    except Exception:  # observability is best-effort; never break the skill
        logger.debug("subprocess tracing unavailable; continuing without spans", exc_info=True)


__all__ = [
    "DEFAULT_BASE_URL",
    "LLMConfigError",
    "LLMSettings",
    "call_json",
    "install_tracing",
    "reset_client_cache",
    "resolve_max_tokens",
    "resolve_temperature",
    "strip_fence",
]
