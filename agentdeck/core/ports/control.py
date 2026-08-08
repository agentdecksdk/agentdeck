"""Cross-process run control: a signal addressed by ``run_id`` alone.

No ``RunContext`` on the port methods — ``run_id`` is globally unique, and a caller reaching for
a run it did not start (a second terminal, an operator's dashboard) has nothing else to offer.
Same reason the port carries ``reason``: the run's own loop records the request in the log, so
the words travel with the signal or are lost.

A run notices a signal at a safe point, through :class:`Gate`, and nowhere else.
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

Bounds control reads by time instead of token count: a 500-chunk answer polled per chunk costs
500 reads answering "no" 499 times, a round trip each on a network-backed port. The cost is
latency only — a signal is still honored at a safe point, up to one interval late. 200ms is
picked against a human's cancel click (still reads as instant) and a ~30ms token stream.
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
        """Record ``sig`` for ``run_id``, replacing whatever was pending. Idempotent.

        Signaling an ended run is harmless by construction, not by a check: nothing polls the gate
        once the run loop exits. ``RESUME`` lifts a pause rather than instructing a live run — it
        replaces the pending ``PAUSE`` so a resumed run does not stop at its first safe point.
        """

    @abstractmethod
    async def poll(self, run_id: str) -> ControlSignal | None:
        """The signal currently pending for ``run_id``, or ``None``."""


class ControlSignalled(Exception):  # noqa: N818 — not an error: a signal honored exactly as asked
    """Raised by :meth:`Gate.checkpoint` when the run must act on a signal at this safe point.

    :attr:`payloads` is the whole record of that act — request, observation, effect — built here
    so every engine adapter tells the same story. An adapter yields them in order and stops
    reading its engine; it never mints a kind, and never decides what pausing means.
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
    """One run's cooperative safe point. Bound to a ``run_id`` by the Runtime, never by the
    caller; with no ``control`` port (the default) ``checkpoint()`` is a no-op.

    Nothing here ever parks a run: a checkpoint reads at most one pending signal, then returns or
    raises. A pause is not a wait *at* the gate — the run unwinds to the Runtime, which records
    ``run.paused`` and lets the process go, so the pause outlives the process and any worker can
    lift it.

    ``clock`` is monotonic, injected so a test can assert the read bound instead of sitting
    through an interval.
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
