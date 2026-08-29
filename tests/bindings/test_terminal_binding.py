"""`Terminal.stdio()` end to end (`docs/design/protocols/bindings.md`, `rulings.md` 34, 35):
a scripted turn through an interrupt and an answer to completion, a real SIGINT cancelling an
in-flight run, and the ambiguous-target refusal at `build()`.
"""

from __future__ import annotations

import asyncio
import io
import os
import signal
import threading

import pytest

from agentdeck import WorkflowCtx, workflow
from agentdeck.adapters.bindings.terminal.binding import Terminal, TerminalBinding
from agentdeck.authoring import Agent
from agentdeck.bindings import ProtocolGateway
from agentdeck.core.status import RunStatus
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


async def _pending(ctx: WorkflowCtx, _: str) -> str:
    return await ctx.ask("proceed?")


class _BlockingStdin:
    """Yields ``lines`` in order, then blocks past the point a test sends SIGINT so the run
    stays in flight long enough to signal it  -  cancelling the awaiting coroutine does not stop
    the worker thread `asyncio.to_thread` already started, so :meth:`release` lets it return
    before the test itself does, and the timeout is a safety net if a test forgets to.
    """

    def __init__(self, *lines: str) -> None:
        self._lines = list(lines)
        self._release = threading.Event()

    def readline(self) -> str:
        if self._lines:
            return self._lines.pop(0)
        self._release.wait(timeout=5)
        return ""

    def release(self) -> None:
        self._release.set()


async def _wait_until(predicate, *, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not await predicate():
            await asyncio.sleep(0.005)


def test_terminal_stdio_is_the_factory_for_terminal_binding():
    binding = Terminal.stdio(target="Greeter")
    assert isinstance(binding, TerminalBinding)
    assert binding.info.kind == "surface"
    assert binding.info.advertises == {"streaming", "text", "hitl", "control.cancel"}


async def test_a_turn_an_interrupt_and_an_answer_reach_completion_matching_the_snapshot():
    deck = Deck(workflows=[workflow(_survey, name="Survey")])
    gateway = ProtocolGateway(deck)
    stdout = io.StringIO()
    binding = TerminalBinding(target="Survey", stdin=io.StringIO("kites\n2\n"), stdout=stdout)
    endpoint = binding.build(gateway)

    async with deck:
        await endpoint.run()

    assert stdout.getvalue() == ("> ? pick a color for kites?\n  1) red\n  2) blue\n> -- run.completed --\n> ")


async def test_an_out_of_range_choice_is_refused_and_re_prompts_instead_of_raising():
    """`run.answer` raises `ValueError` for a choice outside the declared options: the one path
    `_answer` exists to catch, so a fat-fingered number cannot crash the whole session.
    """
    deck = Deck(workflows=[workflow(_survey, name="Survey")])
    gateway = ProtocolGateway(deck)
    stdout = io.StringIO()
    binding = TerminalBinding(target="Survey", stdin=io.StringIO("kites\n9\n2\n"), stdout=stdout)
    endpoint = binding.build(gateway)

    async with deck:
        await endpoint.run()

    transcript = stdout.getvalue()
    assert "! this run is waiting for one of" in transcript
    assert transcript.endswith("-- run.completed --\n> ")


async def test_two_agents_and_no_target_raises_config_error_naming_both():
    deck = Deck(
        agents=[
            Agent(name="Alpha", instructions="."),
            Agent(name="Bravo", instructions="."),
        ]
    )
    gateway = ProtocolGateway(deck)
    binding = TerminalBinding()

    with pytest.raises(ConfigError) as excinfo:
        binding.build(gateway)

    assert "Alpha" in str(excinfo.value)
    assert "Bravo" in str(excinfo.value)


async def test_sigint_mid_run_cancels_the_run_and_exits_cleanly():
    deck = Deck(workflows=[workflow(_pending, name="Pending")])
    gateway = ProtocolGateway(deck)
    stdin = _BlockingStdin("go\n")
    binding = TerminalBinding(target="Pending", stdin=stdin, stdout=io.StringIO())

    async with deck:
        endpoint = binding.build(gateway)
        await binding.start()
        task = asyncio.ensure_future(endpoint.run())
        try:
            await _wait_until(lambda: _is_waiting(gateway))
            os.kill(os.getpid(), signal.SIGINT)
            # The Exposure's own shutdown awaits the stdio task the same way (`exposure.py`
            # `_lifecycle`): SIGINT cancels it, `run.cancel()` runs first, then it re-raises.
            with pytest.raises(asyncio.CancelledError):
                await task
            runs = await gateway.list_runs()
            assert await runs[0].status() == RunStatus.CANCELLED
        finally:
            await binding.stop()
            stdin.release()


async def _is_waiting(gateway: ProtocolGateway) -> bool:
    runs = await gateway.list_runs()
    return bool(runs) and await runs[0].status() == RunStatus.WAITING_ANSWER
