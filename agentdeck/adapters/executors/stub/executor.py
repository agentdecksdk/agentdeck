"""The engine that plays a script  -  the reference implementation of ``Executor``.

An invocable's ``native`` is the script: payloads to yield in order, with any exception in
the sequence raised where it sits. That covers every way a run can end  -  completes, fails
mid-stream, interrupts, or stops without a terminal event  -  with no model, no network and
no timing, which is why it stays the contract suite's fastest engine rather than a
placeholder for a real one. A ``RunInterrupted`` step splits the script in two: ``start``
plays up to and including it, then stops (mirroring a real engine suspending); ``resume``
plays whatever comes after it  -  so one script expresses both halves of a suspend/resume
case without a second field on ``InvocableSpec``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from agentdeck.core.control import ControlSignalled
from agentdeck.core.events import RunInterrupted
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import Executor
from agentdeck.core.status import Play, continuation_of
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload

type Step = KnownPayload | Exception
"""One scripted step: a payload to yield, or an exception to raise at that point."""


class StubExecutor(Executor):
    """Plays ``spec.native`` back as a run. Scripts are reusable: payloads are immutable.

    Honors the gate between steps, so run control is part of what this executor models rather
    than something only a real one can be tested against.
    """

    name: ClassVar[str] = "stub"
    suspendable: ClassVar[bool] = True

    async def execute(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        """Play the script, or  -  when the log says an interrupt was just answered  -  whatever
        the script has after that interrupt. A lifted pause replays from the top, which is what
        a scripted run has instead of a checkpoint to come back to."""
        for step in _resolve_script(spec, history, ctx.run_id):
            if isinstance(step, Exception):
                raise step
            yield step
            if isinstance(step, RunInterrupted):
                return  # suspend here; the answered play picks up after this
            try:
                await ctx.gate.checkpoint()
            except ControlSignalled as signalled:
                # Between two steps is this executor's stream_item boundary  -  the same safe
                # point a real one has between two translated items, which is what lets the
                # contract suite hold both to one control contract.
                for payload in signalled.payloads:
                    yield payload
                return


def _resolve_script(spec: InvocableSpec, history: Sequence[Event], run_id: str) -> tuple[Step, ...]:
    """The steps this play owes: all of them, or the tail after the interrupt an answer just
    landed on."""
    script = _script_of(spec)
    if continuation_of(history, run_id).play is not Play.ANSWER:
        return script
    seen = False
    rest: list[Step] = []
    for step in script:
        if seen:
            rest.append(step)
        seen = seen or isinstance(step, RunInterrupted)
    return tuple(rest)


def stub_spec(name: str, *steps: Step, kind: InvocableKind = InvocableKind.AGENT) -> InvocableSpec:
    """A scripted invocable. Leave ``run.started`` out  -  the Runtime opens every run itself."""
    return InvocableSpec(name=name, kind=kind, executor=StubExecutor.name, native=steps)


def _script_of(spec: InvocableSpec) -> tuple[Step, ...]:
    """A misconfigured invocable is the caller's mistake, not a run that failed  -  so this is a
    ``ConfigError`` at the adapter's edge rather than a stdlib type leaking into ``run.failed``."""
    script = spec.native
    if isinstance(script, str) or not isinstance(script, Sequence):
        raise ConfigError(
            f"{spec.name!r} has no stub script: expected a sequence of payloads in native, got {type(script).__name__}"
        )
    return tuple(script)


__all__ = ["Step", "StubExecutor", "stub_spec"]
