"""The tracing surface :class:`~agentdeck.adapters.telemetry.langfuse.sink.LangfuseSink`
writes to: one root observation per run, children under it, each finished exactly once.

Narrow on purpose. The event-to-observation mapping is the part of this adapter that can be
wrong, and stating it as two protocols is what lets a recorder stand in for Langfuse in a
test — no keys, no collector, no network — while the SDK stays behind one module.

Observations are opened and finished as separate calls rather than through a context
manager: a sink sees a run as interleaved events, so the span that opens on
``tool.call.started`` has to outlive the ``emit`` that opened it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agentdeck.core.events import Usage

# Langfuse's observation types, restricted to the ones a canonical event can justify.
ObservationKind = Literal["agent", "chain", "tool", "span", "generation"]

# Langfuse's observation levels, verbatim — an adapter that renamed them would only make
# the mapping to the backend's own vocabulary harder to check.
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


class Observation(Protocol):
    """One open observation. It may open children and must be finished exactly once."""

    def child(
        self,
        name: str,
        *,
        kind: ObservationKind,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observation:
        """Open a nested observation under this one."""

    def finish(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        level: Level | None = None,
        status: str | None = None,
        usage: Usage | None = None,
    ) -> None:
        """Close this observation, recording how it ended.

        ``usage`` is only accounted on a ``generation``; on any other kind the backend
        ignores it, so a caller with a token total to report opens one.
        """


class Tracer(Protocol):
    """Opens the root of a trace — the only observation that carries the trace's identity."""

    def root(
        self,
        name: str,
        *,
        kind: ObservationKind,
        trace_key: str,
        session_id: str | None,
        user_id: str | None,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Observation:
        """Open the root observation of the trace ``trace_key`` identifies.

        Equal ``trace_key``s belong to the same trace, whichever process opened them — that
        is what makes a run suspended in one worker and resumed in another one trace rather
        than two.
        """

    def flush(self) -> None:
        """Ship what is still buffered, blocking until it has left or been given up on.

        Called once, when the sink is closed. Blocking because the SDK's own flush is, and
        pretending otherwise would only hide where the waiting happens: the sink puts this on a
        worker thread so the deadline bounding it stays honest.
        """


__all__ = ["Level", "Observation", "ObservationKind", "Tracer"]
