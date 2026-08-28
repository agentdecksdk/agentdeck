"""How a run notices it was signaled, and what honoring one records.

The mirror image of :class:`~agentdeck.core.reporting.Reporter`  -  control flows in on
``RunContext`` through :class:`Gate`, updates flow out the same way. Neither is a port: an outer
ring implements :class:`~agentdeck.core.ports.control.ControlPort`, the transport that carries a
signal between processes, while the vocabulary a caller signals in and the policy that turns one
honored signal into three events are core's own.

The three verbs  -  ``cancel``, ``pause``, ``resume``  -  match the event schema's ``ControlVerb``
one for one (``steer`` is a mailbox rather than a signal, and is not built).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from agentdeck.core.events import ControlObserved, ControlRequested, RunCancelled, RunPaused
from agentdeck.core.status import Action, RunStatus, decide

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from agentdeck.core.events import ControlVerb, KnownPayload, SafePoint
    from agentdeck.core.ports.control import ControlPort

CONTROL_POLL_INTERVAL = 0.2
"""Seconds a :class:`Gate` may reuse the answer it already has (issue #85).

Bounds control reads by time instead of token count: a 500-chunk answer polled per chunk costs
500 reads answering "no" 499 times, a round trip each on a network-backed port. The cost is
latency only  -  a signal is still honored at a safe point, up to one interval late. 200ms is
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


class ControlSignalled(Exception):  # noqa: N818  -  not an error: a signal honored exactly as asked
    """Raised by :meth:`Gate.checkpoint` when the run must act on a signal at this safe point.

    :attr:`payloads` is the whole record of that act  -  request, observation, effect  -  built here
    so every engine adapter tells the same story. An adapter yields them in order and stops
    reading its engine; it never mints a kind, and never decides what pausing means.
    """

    verb: ClassVar[ControlVerb]

    def __init__(self, id: str, safe_point: SafePoint, reason: str | None = None) -> None:
        super().__init__(f"run {id} was signaled {self.verb} at a {safe_point} safe point")
        self.id = id
        self.safe_point: SafePoint = safe_point  # annotated: an inferred attribute widens to str
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


# Which exception carries which effect  -  declared, not branched on, so the only place a verb is
# tested against a name is a table. ``POLICY`` says *whether* to halt; this says how.
_HALTED_BY: Mapping[Signal, type[ControlSignalled]] = {
    Signal.CANCEL: RunCancelledError,
    Signal.PAUSE: RunPausedError,
}


class Gate:
    """One run's cooperative safe point. Bound to an ``id`` by the Runtime, never by the
    caller; with no ``control`` port (the default) ``checkpoint()`` is a no-op.

    Nothing here ever parks a run: a checkpoint reads at most one pending signal, then returns or
    raises. A pause is not a wait *at* the gate  -  the run unwinds to the Runtime, which records
    ``run.paused`` and lets the process go, so the pause outlives the process and any worker can
    lift it.

    ``clock`` is monotonic, injected so a test can assert the read bound instead of sitting
    through an interval.
    """

    def __init__(
        self,
        control: ControlPort | None = None,
        id: str = "",
        *,
        poll_interval: float = CONTROL_POLL_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval < 0:
            raise ValueError(f"poll_interval must be 0 or more seconds, got {poll_interval}")
        self._control = control
        self._id = id
        self._poll_interval = poll_interval
        self._clock = clock
        self._polled_at: float | None = None

    async def checkpoint(self, safe_point: SafePoint = "stream_item") -> None:
        """Return immediately, unless a signal is pending  -  then raise :class:`ControlSignalled`.

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
        pending = await self._control.poll(self._id)
        ruling = decide(RunStatus.RUNNING, None if pending is None else pending.verb)
        if pending is None or ruling.action is not Action.HALT:
            # The two explicit no-ops of the ``RUNNING`` row: an empty port, and a RESUME, which
            # is a lifted pause rather than an instruction  -  a run that is already running has
            # nothing to do about one, and reading it again next interval costs nothing.
            return
        if ruling.consume:
            # Before the raise, because the raise is what records the effect: an intent left
            # pending behind an honored one would be honored a second time on the next resume.
            await self._control.consume(self._id, pending.verb)
        raise _HALTED_BY[pending.verb](self._id, safe_point, pending.reason)

    async def checkpoint_cancel_only(self, safe_point: SafePoint) -> None:
        """Like :meth:`checkpoint`, but honors CANCEL alone and never touches PAUSE.

        The safe point a THREAD-executed tool body could never offer on its own: it has no await
        point while its worker runs, so the native executor asks this once, right after the body
        returns, before recording completion. Consuming a pending PAUSE here, with no parked body
        to leave it suspended in, would make it unresumable  -  so it is left for a real
        checkpoint to honor instead, exactly as a leaf tool with none of its own already leaves it
        today.
        """
        if self._control is None:
            return
        pending = await self._control.poll(self._id)
        if pending is None or pending.verb is not Signal.CANCEL:
            return
        await self._control.consume(self._id, pending.verb)
        raise RunCancelledError(self._id, safe_point, pending.reason)
