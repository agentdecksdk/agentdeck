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
from typing import Any, TextIO
from uuid import uuid4

from agentdeck.bindings import PROTOCOL_SPI_VERSION, BindingInfo, DeckGateway, StdioEndpoint
from agentdeck.core.events import (
    KNOWN_KINDS,
    MessageCompleted,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunInterrupted,
    TextDelta,
)
from agentdeck.errors import ConfigError, RunStateError

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
        self._gateway: DeckGateway | None = None

    def _require_gateway(self) -> DeckGateway:
        assert self._gateway is not None, "build() must run before the stdio loop starts"
        return self._gateway

    def build(self, gateway: DeckGateway) -> StdioEndpoint:
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
        """No background work."""

    async def stop(self) -> None:
        """No background work."""

    async def _run(self) -> None:
        # Typed loosely on purpose: ``Run`` is ``agentdeck.deck``'s, which this binding's own
        # import-linter contract forbids naming even under ``TYPE_CHECKING`` (matches Native).
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
        except _EofError:
            if run is not None:
                await run.cancel()
        except asyncio.CancelledError:
            # Idle: return cleanly, no exception reaches the caller's asyncio.run(). Mid-run:
            # record the cancel, then re-raise for asyncio.Runner to convert it below.
            if run is None:
                return
            with contextlib.suppress(RunStateError):  # already ended; nothing left to cancel
                await run.cancel()
            self._write("\n")
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
