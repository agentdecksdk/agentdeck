"""``Terminal.stdio()``: the simplest binding, one session per process over stdin/stdout
(``docs/design/protocols/bindings.md``, ``rulings.md`` 34, 35).

Ctrl-C needs no handler here: Python's own ``asyncio.Runner`` (3.12+) cancels the running main
task on SIGINT, surfacing as ``asyncio.CancelledError`` wherever this is awaiting; a second
handler would only override that one, or uvicorn's own on an HTTP exposure.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
from typing import TYPE_CHECKING, Any, TextIO
from uuid import uuid4

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, DeckGateway, StdioEndpoint
from agentdeck.errors import ConfigError, InputError, RunStateError

if TYPE_CHECKING:
    from agentdeck import Event, Run
    from agentdeck.bindings import Binding

_ADVERTISES = frozenset({"streaming", "text", "hitl", "control.cancel"})
_TERMINAL_KINDS = ("run.completed", "run.failed", "run.cancelled")


class _EndOfInputError(Exception):
    """stdin ended: Ctrl-D, or a scripted stdin ran out."""


class _StdinLines:
    """One reader this binding owns, so a blocked read never owns the process.

    The read blocks in a daemon thread and the loop only ever awaits a queue, which cancels like
    anything else. A daemon thread does not hold up interpreter exit, so shutdown needs no
    ``os._exit`` to escape a thread parked in ``readline()`` on a terminal that never sends EOF.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        self._started = False

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        while True:
            line = self._stream.readline()
            loop.call_soon_threadsafe(self._lines.put_nowait, line or None)
            if not line:
                return

    async def next(self) -> str:
        if not self._started:
            self._started = True
            thread = threading.Thread(
                target=self._pump, args=(asyncio.get_running_loop(),), name="agentdeck-terminal-stdin", daemon=True
            )
            thread.start()
        line = await self._lines.get()
        if line is None:
            raise _EndOfInputError
        return line.rstrip("\n")


class _TerminalBinding:
    """``kind="surface"``: prompts on stdin, prints the run's own text back to stdout. No auth,
    store or background task (``rulings.md`` 35).
    """

    def __init__(
        self,
        *,
        target: str | None = None,
        session_id: str | None = None,
        name: str = "terminal",
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.info = BindingInfo(
            name=name,
            kind="surface",
            transport="stdio",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
        )
        self._target = target
        self._session_id = session_id or uuid4().hex
        self._stdout = stdout if stdout is not None else sys.stdout
        self._input = _StdinLines(stdin if stdin is not None else sys.stdin)
        self._gateway: DeckGateway | None = None

    def build(self, gateway: DeckGateway) -> StdioEndpoint:
        """Resolve and validate the target here, so a name that does not exist fails at
        ``expose()`` rather than after someone types their first message.
        """
        self._gateway = gateway
        names = sorted(target.name for target in gateway.targets())
        if self._target is None:
            if len(names) != 1:
                raise ConfigError(
                    f"Terminal.stdio() needs `target=`: this deck holds {len(names)} targets, so "
                    f"there is nothing to default to. Available: {names}."
                )
            self._target = names[0]
        elif self._target not in names:
            raise ConfigError(f"no target named {self._target!r} in this deck. Available: {names}.")
        return StdioEndpoint(self._run)

    async def start(self) -> None:
        """No background work."""

    async def stop(self) -> None:
        """No background work."""

    async def _run(self) -> None:
        gateway = self._require_gateway()
        target = self._target
        assert target is not None, "build() resolves the target before the loop runs"
        run: Run | None = None
        try:
            while True:
                line = await self._readline("> ")
                run = await gateway.start(target, line, session_id=self._session_id)
                await self._drive(run)
                run = None
        except _EndOfInputError:
            if run is not None:
                await run.cancel()
        except asyncio.CancelledError:
            # Idle: return cleanly, so nothing reaches the caller's asyncio.run(). Mid-run:
            # record the cancel, then re-raise for asyncio.Runner to convert.
            if run is None:
                return
            with contextlib.suppress(RunStateError):  # already ended; nothing left to cancel
                await run.cancel()
            self._write("\n")
            raise

    def _require_gateway(self) -> DeckGateway:
        if self._gateway is None:
            raise RuntimeError("build() has not run: this binding has no gateway")
        return self._gateway

    async def _drive(self, run: Run) -> None:
        """One loop, not recursion: an interrupt answers and then re-tails from the next seq, so
        a workflow asking a hundred questions costs a hundred iterations and no stack.
        """
        from_seq = 0
        while True:
            interrupted = await self._tail(run, from_seq)
            if interrupted is None:
                return
            await self._answer(run, interrupted.payload)
            from_seq = interrupted.seq + 1

    async def _tail(self, run: Run, from_seq: int) -> Event | None:
        """The segment's events, returning the interrupt that ended it, or None at a terminal."""
        # `kind` and getattr, not isinstance: narrowing the payload union would mean importing
        # `agentdeck.core`, which this binding's import contract forbids.
        async for event in run.events(from_seq=from_seq, follow=True):
            kind = getattr(event.payload, "kind", None)
            if kind == "text.delta":
                self._write(str(getattr(event.payload, "text", "")))
            elif kind == "message.completed":
                self._write("\n")
            elif kind == "run.interrupted":
                return event
            elif kind in _TERMINAL_KINDS:
                self._write(f"-- {kind} --\n")
        return None

    async def _answer(self, run: Run, interrupt: Any) -> None:
        """Ask, answer, and re-ask on refusal: an option typed out of range is not one
        ``run.answer`` can carry, and it raises ``InputError`` rather than hanging the run.
        """
        while True:
            value = await self._ask(interrupt)
            try:
                await run.answer(value)
                return
            except InputError as error:
                self._write(f"! {error}\n")

    async def _ask(self, interrupt: Any) -> Any:
        """Numbered prompt in the interrupt payload's own option order, never re-sorted, so the
        number a person types always names the option they read.
        """
        raw_options = interrupt.payload.get("options")
        options = raw_options if isinstance(raw_options, list) else None
        self._write(f"? {interrupt.payload.get('question')}\n")
        for index, option in enumerate(options or [], start=1):
            self._write(f"  {index}) {option}\n")
        choice = await self._readline("> ")
        if options:
            try:
                position = int(choice)
            except ValueError:
                position = -1
            if 1 <= position <= len(options):
                return options[position - 1]
        return choice

    async def _readline(self, prompt: str) -> str:
        self._write(prompt)
        return await self._input.next()

    def _write(self, text: str) -> None:
        self._stdout.write(text)
        self._stdout.flush()


class Terminal:
    """The terminal surface: the one (stdin, stdout) pair it supports
    (``docs/design/protocols/bindings.md``)."""

    @staticmethod
    def stdio(
        *,
        target: str | None = None,
        session_id: str | None = None,
        name: str = "terminal",
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> Binding:
        """`target` names any agent or workflow in the deck, validated at `expose()`. Omit it only
        when the deck holds exactly one target. `name` distinguishes a second instance.
        """
        return _TerminalBinding(
            target=target, session_id=session_id, name=name, stdin=stdin, stdout=stdout
        )


__all__ = ["Terminal"]
