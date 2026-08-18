"""The engine that plays a script  -  the reference implementation of ``EnginePort``.

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
from typing import TYPE_CHECKING, Any, ClassVar

from agentdeck.core.control import ControlSignalled
from agentdeck.core.events import RunInterrupted
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.core.ports import EnginePort
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentdeck.core.content import Input
    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload

type Step = KnownPayload | Exception
"""One scripted step: a payload to yield, or an exception to raise at that point."""


class StubEngine(EnginePort):
    """Plays ``spec.native`` back as a run. Scripts are reusable: payloads are immutable.

    Honors the gate between steps, so run control is part of what this engine models rather
    than something only a real one can be tested against.
    """

    engine: ClassVar[str] = "stub"

    async def start(
        self,
        spec: InvocableSpec,
        input: Input,
        history: Sequence[Event],
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        for step in _script_of(spec):
            if isinstance(step, Exception):
                raise step
            yield step
            if isinstance(step, RunInterrupted):
                return  # suspend here; resume() plays whatever the script has after this
            try:
                await ctx.gate.checkpoint()
            except ControlSignalled as signalled:
                # Between two steps is this engine's stream_item boundary  -  the same safe
                # point a real engine has between two translated items, which is what lets
                # the contract suite hold both to one control contract.
                for payload in signalled.payloads:
                    yield payload
                return

    async def resume(
        self,
        spec: InvocableSpec,
        thread_id: str,
        value: Any,
        ctx: RunContext,
    ) -> AsyncGenerator[KnownPayload, None]:
        after_interrupt = False
        for step in _script_of(spec):
            if not after_interrupt:
                after_interrupt = isinstance(step, RunInterrupted)
                continue
            if isinstance(step, Exception):
                raise step
            yield step


def stub_spec(name: str, *steps: Step, kind: InvocableKind = InvocableKind.AGENT) -> InvocableSpec:
    """A scripted invocable. Leave ``run.started`` out  -  the Runtime opens every run itself."""
    return InvocableSpec(name=name, kind=kind, engine=StubEngine.engine, native=steps)


def _script_of(spec: InvocableSpec) -> tuple[Step, ...]:
    """A misconfigured invocable is the caller's mistake, not a run that failed  -  so this is a
    ``ConfigError`` at the adapter's edge rather than a stdlib type leaking into ``run.failed``."""
    script = spec.native
    if isinstance(script, str) or not isinstance(script, Sequence):
        raise ConfigError(
            f"{spec.name!r} has no stub script: expected a sequence of payloads in native, got {type(script).__name__}"
        )
    return tuple(script)


__all__ = ["Step", "StubEngine", "stub_spec"]
