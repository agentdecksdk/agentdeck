"""The execution boundary: play a run, yield payloads until it ends.

An executor yields payloads and nothing else  -  no envelopes, no ``seq``, no namespace  -  so
ordering and isolation stay with the Runtime and an executor cannot get them wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload
    from agentdeck.core.invocable import InvocableSpec


class Executor(ABC):
    """One way of executing a target: an SDK, a graph runtime, or AgentDeck's own.

    A run ends in exactly one of three ways: a terminal payload (``run.completed`` /
    ``run.failed`` / ``run.cancelled``), a suspending one (``run.interrupted`` / ``run.paused``)
    whose terminal event comes after resume, or a raised exception. Stopping after anything else
    is a contract violation the Runtime records as ``run.failed``. A terminal payload ends the
    run there and then  -  anything yielded after one is discarded rather than logged.

    ``run.started`` is the Runtime's to emit: it carries context an executor never sees. An
    executor emits existing kinds or namespaced ``custom``; minting a kind is core's job.

    An async generator, not any async iterable  -  the Runtime closes the stream when it stops
    reading early, so an executor gets its ``finally`` blocks either way.
    """

    name: ClassVar[str]
    """Matches ``InvocableSpec.executor``; the Runtime selects the executor on it."""

    suspendable: ClassVar[bool]
    """Whether a run on this executor can be paused and later continued.

    Declared, not implemented: pause and resume are the Runtime's, through the control port and
    the cooperative :class:`~agentdeck.core.control.Gate`, so what an executor says here is only
    whether it reaches a point where either can be applied. It is one flag rather than two
    because a run that can stop but never continue is not an AgentDeck pause
    (``docs/design/execution-api.md``), and :func:`~agentdeck.core.status.can_of` is the only
    reader.
    """

    @abstractmethod
    def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        """Play one run. ``history`` is the log so far, which is the record of the session  -
        an executor that keeps its own execution state loads that itself (ADR-D5).
        """

    @abstractmethod
    def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        """Continue a run this executor suspended with ``run.interrupted(thread_id=...)``.

        ``value`` answers the interrupt; ``thread_id`` is whatever the executor put on that
        event, opaque to the Runtime. An executor with nothing to suspend on raises rather than
        yielding  -  there is no run to continue. The Runtime calls this only after confirming the
        run is waiting on a human answer, so a well-behaved one never sees a stray or duplicate.
        """
