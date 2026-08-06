"""Cross-process run control: a signal addressed by ``run_id`` alone.

No ``RunContext`` parameter on the port methods — ``run_id`` is already globally unique, and
a caller reaching for a run it did not start (a second terminal, an operator's dashboard) has
nothing else to offer. That is also why the port carries the caller's ``reason``: the run's
own loop is what records the request in the log, so the words have to travel with the signal
or be lost.

The three verbs a run can be signaled with — ``cancel``, ``pause``, ``resume`` — match the
event schema's ``ControlVerb`` values one for one (``steer`` is a mailbox rather than a
signal, and is not built). A run notices a signal at a safe point, through :class:`Gate`, and
nowhere else.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from agentdeck.core.events import ControlObserved, ControlRequested, RunCancelled, RunPaused

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentdeck.core.events import ControlVerb, KnownPayload, SafePoint

CONTROL_POLL_INTERVAL = 0.2
"""Seconds a :class:`Gate` may reuse the answer it already has (issue #85).

A run's control reads are bounded by this interval rather than by the rate the model emits
tokens: a 500-chunk answer polled once per chunk costs 500 reads whose answer is "no" 499
times, and a network-backed port pays a round trip for each. What it costs is latency, and
only latency — a signal is still acted on *at* a safe point, up to one interval after it was
recorded. 200ms is picked against the two things that have to live with it: a human's cancel
click (a third of a second still reads as instant) and a real token stream (~30ms per chunk,
so one read stands in for six).
"""


class Signal(StrEnum):
    """What a caller can ask an in-flight run to do."""

    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass(frozen=True, slots=True)
class ControlSignal:
    """The signal pending for one run, and why the caller asked for it."""

    verb: Signal
    reason: str | None = None


class ControlPort(ABC):
    """Write and read the pending signal for one run, from any process that knows its id."""

    @abstractmethod
    async def signal(self, run_id: str, sig: Signal, reason: str | None = None) -> None:
        """Record ``sig`` for ``run_id``, replacing whatever was pending. Idempotent: signaling
        the same verb twice changes nothing. Signaling a run that already ended is harmless by
        construction, not by an active check — nothing polls the gate once the run loop has
        exited, so the signal simply sits unread.

        ``RESUME`` lifts a pause rather than instructing a live run: it replaces the pending
        ``PAUSE`` so a resumed run does not stop again at its first safe point.
        """

    @abstractmethod
    async def poll(self, run_id: str) -> ControlSignal | None:
        """The signal currently pending for ``run_id``, or ``None``."""


class ControlSignalled(Exception):  # noqa: N818 — not an error: a signal honored exactly as asked
    """Raised by :meth:`Gate.checkpoint` when the run must act on a signal at this safe point.

    :attr:`payloads` is the whole record of that act, built here so every engine adapter tells
    the same story with the same kinds: the request, the observation, and the verb's own
    effect. An adapter yields them in order and stops reading its engine — it never mints a
    kind of its own, and never decides what pausing or cancelling means.
    """

    verb: ClassVar[ControlVerb]

    def __init__(self, run_id: str, safe_point: SafePoint, reason: str | None = None) -> None:
        super().__init__(f"run {run_id} was signaled {self.verb} at a {safe_point} safe point")
        self.run_id = run_id
        self.safe_point = safe_point
        self.reason = reason

    @property
    def payloads(self) -> tuple[KnownPayload, ...]:
        """The three phases of one honored signal, in the order they have to be recorded."""
        return (
            ControlRequested(verb=self.verb, reason=self.reason),
            ControlObserved(verb=self.verb, safe_point=self.safe_point),
            self._effect(),
        )

    def _effect(self) -> KnownPayload:
        raise NotImplementedError


class RunCancelledError(ControlSignalled):
    """The run was signaled CANCEL: its effect is terminal, and terminal is not resumable."""

    verb: ClassVar[ControlVerb] = "cancel"

    def _effect(self) -> KnownPayload:
        return RunCancelled(reason=self.reason)


class RunPausedError(ControlSignalled):
    """The run was signaled PAUSE: suspended at this safe point, resumable from the log."""

    verb: ClassVar[ControlVerb] = "pause"

    def _effect(self) -> KnownPayload:
        return RunPaused(reason=self.reason)


class Gate:
    """One run's cooperative safe point.

    With no ``control`` port (the default), ``checkpoint()`` is a no-op — every existing run
    that never wires a ``ControlPort`` behaves exactly as before. A run that does gets one
    bound to its own ``run_id``, built by the Runtime, never by the caller.

    Nothing here ever parks a run: a checkpoint reads at most one pending signal and then
    either returns or raises. A pause is not a wait *at* the gate — the run unwinds to the
    Runtime, which records ``run.paused`` and lets the process go, so the pause outlives the
    process and can be lifted by whichever worker picks the run up.

    ``clock`` is a monotonic source, injected so a test can assert the read bound as a fact
    rather than sit through an interval.
    """

    def __init__(
        self,
        control: ControlPort | None = None,
        run_id: str = "",
        *,
        poll_interval: float = CONTROL_POLL_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval < 0:
            raise ValueError(f"poll_interval must be 0 or more seconds, got {poll_interval}")
        self._control = control
        self._run_id = run_id
        self._poll_interval = poll_interval
        self._clock = clock
        self._polled_at: float | None = None

    async def checkpoint(self, safe_point: SafePoint = "stream_item") -> None:
        """Return immediately, unless a signal is pending — then raise :class:`ControlSignalled`.

        The first checkpoint of a run always reads, so a signal that beat the run out of the
        gate is honored at once; after that an answer is reused for ``poll_interval``, which is
        what bounds a run's control reads by time instead of by token count.
        """
        if self._control is None:
            return
        now = self._clock()
        if self._polled_at is not None and now - self._polled_at < self._poll_interval:
            return
        self._polled_at = now
        pending = await self._control.poll(self._run_id)
        if pending is None or pending.verb is Signal.RESUME:
            # RESUME is a lifted pause, not an instruction: a run that is already running has
            # nothing to do about it, and reading it again next interval costs nothing.
            return
        if pending.verb is Signal.CANCEL:
            raise RunCancelledError(self._run_id, safe_point, pending.reason)
        raise RunPausedError(self._run_id, safe_point, pending.reason)


__all__ = [
    "CONTROL_POLL_INTERVAL",
    "ControlPort",
    "ControlSignal",
    "ControlSignalled",
    "Gate",
    "RunCancelledError",
    "RunPausedError",
    "Signal",
]
