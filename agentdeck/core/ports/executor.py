"""The execution boundary: play a run, yield payloads until it ends.

An executor yields payloads and nothing else  -  no envelopes, no ``seq``, no namespace  -  so
ordering and isolation stay with the Runtime and an executor cannot get them wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

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
    def execute(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        """Play one run of ``spec``.

        ``input`` is what the run was opened with, unchanged however many times it is played.
        ``history`` is the log so far, which is the record of the session  -  an executor that
        keeps its own execution state loads that itself (ADR-D5)  -  and it is also what says
        which of the three plays this call is
        (:func:`~agentdeck.core.status.play_of`):

        =========  =================================================================
        fresh      the run has not run yet, or ran and ended
        replay     a pause was lifted, so the run is played again from ``input``
        answer     an interrupt was answered, and the value is on the last
                   ``run.resumed`` (:func:`~agentdeck.core.content.answer_of`)
        =========  =================================================================

        One method rather than a second one for the answer: a paused run is already replayed
        through here, the log already carries both the answer and the ``thread_id`` this
        executor wrote onto its own ``run.interrupted``, and an executor that never suspends
        has nothing to implement twice. An executor that cannot take an answer raises on that
        play  -  the Runtime only ever sends one to a run that suspended.
        """
