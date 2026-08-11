"""The Langfuse SDK boundary: the only module in the package that names ``langfuse``.

Two jobs. :func:`langfuse_sink` answers "is Langfuse configured?" with a sink or with
``None`` — nothing is imported and no client is built until the answer is yes, so an
unconfigured process pays for none of this. :class:`LangfuseTracer` is the SDK-backed
``Tracer``: it opens observations and hands back handles, and every call it makes is
in-memory. Delivery belongs to the SDK's batching span processor, which ships from a
background thread, so ``emit`` never waits on the network; the sink's ``close`` is what makes
that buffer leave the process at shutdown, instead of trusting an ``atexit`` a killed process
never runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentdeck.adapters.telemetry.langfuse.sink import LangfuseSink
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentdeck.adapters.telemetry.langfuse.trace import Level, Observation, ObservationKind
    from agentdeck.core.events import Usage
    from agentdeck.runtime.settings import LangfuseSettings


def langfuse_sink(settings: LangfuseSettings | None = None) -> LangfuseSink | None:
    """The sink to register with the Runtime, or ``None`` when Langfuse has no keys.

    The composition root spreads the result into ``Runtime(sinks=...)``: unconfigured means
    no sink in that list at all, so an unconfigured run never reaches this adapter and never
    pays a queue, a task or an import for it.
    """
    langfuse = settings if settings is not None else get_settings().langfuse
    if not langfuse.enabled:
        return None
    return LangfuseSink(LangfuseTracer(_build_client(langfuse)))


def _build_client(settings: LangfuseSettings) -> Any:
    """Construct the SDK client. Imported here, never at module scope, so the optional
    ``[observability]`` extra stays optional."""
    from langfuse import Langfuse  # ty: ignore[unresolved-import] — [observability] extra

    return Langfuse(
        public_key=settings.public_key,
        secret_key=settings.secret_key,
        base_url=settings.base_url,
        environment=settings.environment,
        debug=settings.debug,
        sample_rate=settings.sample_rate,
    )


class LangfuseTracer:
    """``Tracer`` over the Langfuse SDK, with one trace id derived from each run's key.

    Deriving the trace id instead of letting the SDK mint one does two things. It pins the
    root to the run rather than to whatever span happens to be current — the sink runs on a
    consumer task that inherited its context from a run, and a trace must not end up nested
    under the tracing v1 already does. And it makes the key the identity: the same run
    resumed in another process reopens the same trace instead of starting a second one.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def root(
        self,
        name: str,
        *,
        kind: ObservationKind,
        trace_key: str,
        session_id: str | None,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observation:
        from langfuse import propagate_attributes  # ty: ignore[unresolved-import] — [observability] extra

        # Session, user and trace name are trace-level in Langfuse: they reach the backend as
        # attributes stamped on spans opened inside this context, so the root is opened inside it.
        with propagate_attributes(
            session_id=session_id,
            trace_name=name,
            metadata=dict(metadata) if metadata else None,
        ):
            return _LangfuseObservation(
                self._client.start_observation(
                    trace_context={"trace_id": self._client.create_trace_id(seed=trace_key)},
                    name=name,
                    as_type=kind,
                    input=input,
                )
            )

    def flush(self) -> None:
        """Hand the SDK's buffer to Langfuse now, rather than hoping the process exits cleanly
        enough for its ``atexit`` to do it."""
        self._client.flush()


class _LangfuseObservation:
    """``Observation`` over one Langfuse span handle."""

    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def child(
        self,
        name: str,
        *,
        kind: ObservationKind,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observation:
        return _LangfuseObservation(
            self._span.start_observation(
                name=name, as_type=kind, input=input, metadata=dict(metadata) if metadata else None
            )
        )

    def finish(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        status: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        self._span.update(
            output=output,
            metadata=dict(metadata) if metadata else None,
            level=level,
            status_message=status,
            usage_details=_usage_details(usage),
            cost_details=_cost_details(usage),
        )
        self._span.end()


def _usage_details(usage: Usage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {"input": usage.input_tokens, "output": usage.output_tokens}


def _cost_details(usage: Usage | None) -> dict[str, float] | None:
    if usage is None or usage.usd is None:
        return None
    return {"total": usage.usd}


__all__ = ["LangfuseTracer", "langfuse_sink"]
