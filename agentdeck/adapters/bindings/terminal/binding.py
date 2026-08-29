"""``Terminal.stdio()``: the simplest binding, one session per process over stdin/stdout
(``docs/design/protocols/bindings.md``, ``rulings.md`` 34, 35).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from typing import Any, TextIO
from uuid import uuid4

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, ProtocolGateway, StdioEndpoint
from agentdeck.core.events import (
    KNOWN_KINDS,
    MessageCompleted,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    TextDelta,
)
from agentdeck.errors import ConfigError

_ADVERTISES = frozenset({"streaming", "text", "hitl", "control.cancel"})


class _EofError(Exception):
    """``readline()`` returned nothing: the stream ended (Ctrl-D, or a scripted stdin ran out)."""


class TerminalBinding:
    """``kind="surface"``: prompts on stdin, prints the run's own text back to stdout. No auth,
    store or background task (``rulings.md`` 35).
    """

    def __init__(
        self,
        *,
        target: str | None = None,
        session_id: str | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self.info = BindingInfo(
            name="terminal",
            kind="surface",
            transport="stdio",
            spi_version=PROTOCOL_SPI_VERSION,
            advertises=_ADVERTISES,
            projects=KNOWN_KINDS,
        )
        self._target = target
        self._session_id = session_id or uuid4().hex
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._gateway: ProtocolGateway | None = None
        self._task: asyncio.Task[None] | None = None

    def _require_gateway(self) -> ProtocolGateway:
        assert self._gateway is not None, "build() must run before the stdio loop starts"
        return self._gateway

    def build(self, gateway: ProtocolGateway) -> StdioEndpoint:
        self._gateway = gateway
        if self._target is None:
            agents = sorted(t.name for t in gateway.targets() if t.kind == "agent")
            if len(agents) != 1:
                raise ConfigError(
                    f"Terminal.stdio() needs `target=`: there is not exactly one agent to default "
                    f"to. Agents in this deck: {agents}."
                )
            self._target = agents[0]
        return StdioEndpoint(self._run)

    async def start(self) -> None:
        """Installs SIGINT so Ctrl-C cancels the in-flight run: without a handler, a bare
        ``asyncio.run()`` aborts the whole loop on the signal and never reaches an ``await
        run.cancel()`` (the issue's own pitfall). ``NotImplementedError`` on Windows, where
        ``add_signal_handler`` is unsupported; Ctrl-C there falls back to the interpreter default.
        """
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().add_signal_handler(signal.SIGINT, self._sigint)

    async def stop(self) -> None:
        with contextlib.suppress(NotImplementedError):
            asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)

    def _sigint(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        # Typed loosely on purpose: ``Run`` is ``agentdeck.deck``'s, which this binding's own
        # import-linter contract forbids naming even under ``TYPE_CHECKING`` (matches Native).
        self._task = asyncio.current_task()
        gateway = self._require_gateway()
        assert self._target is not None, "build() must run before the stdio loop starts"
        target = self._target
        run: Any = None
        try:
            while True:
                line = await self._readline("> ")
                run = await gateway.start(target, line, session_id=self._session_id)
                await self._drive(run)
                run = None
        except (_EofError, KeyboardInterrupt):
            if run is not None:
                await run.cancel()
        except asyncio.CancelledError:
            if run is not None:
                await run.cancel()
            raise

    async def _drive(self, run: Any, *, from_seq: int = 0) -> None:
        async for event in run.events(from_seq=from_seq, follow=True):
            match event.payload:
                case TextDelta(text=text):
                    self._write(text)
                case MessageCompleted():
                    self._write("\n")
                case RunInterrupted() as interrupted:
                    await self._answer(run, interrupted, from_seq=event.seq + 1)
                    return
                case RunCompleted() | RunFailed() | RunCancelled():
                    self._write(f"-- {event.kind} --\n")
                case _:
                    pass

    async def _answer(self, run: Any, interrupted: RunInterrupted, *, from_seq: int) -> None:
        """Ask, answer, and re-ask on refusal: an option typed out of range is not one
        ``run.answer`` can carry, and it raises ``ValueError`` rather than hanging the run  -
        this is the one place that answer reaches, so it is also the one place that catches it.
        """
        while True:
            value = await self._ask(interrupted)
            try:
                await run.answer(value)
            except ValueError as error:
                self._write(f"! {error}\n")
                continue
            break
        await self._drive(run, from_seq=from_seq)

    async def _ask(self, interrupted: RunInterrupted) -> Any:
        """Numbered prompt in the interrupt payload's own option order (never re-sorted, so the
        number a person types always names the option they read). ``options`` is a plain list
        or nothing (``_refuses`` in ``runtime/service.py`` reads the same field the same way)."""
        raw_options = interrupted.payload.get("options")
        options = raw_options if isinstance(raw_options, list) else None
        self._write(f"? {interrupted.payload.get('question')}\n")
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
        raw = await asyncio.to_thread(self._stdin.readline)
        if raw == "":
            raise _EofError
        return raw.rstrip("\n")

    def _write(self, text: str) -> None:
        self._stdout.write(text)
        self._stdout.flush()


class Terminal:
    """Factory for the terminal surface: the one (stdin, stdout) pair it supports
    (``docs/design/protocols/bindings.md``)."""

    @staticmethod
    def stdio(*, target: str | None = None, session_id: str | None = None) -> TerminalBinding:
        return TerminalBinding(target=target, session_id=session_id)


__all__ = ["Terminal", "TerminalBinding"]
