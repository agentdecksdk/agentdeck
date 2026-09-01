"""`Terminal.stdio()` end to end (`docs/design/protocols/bindings.md`, `rulings.md` 34, 35):
a scripted turn through an interrupt and an answer to completion, cancelling an in-flight run,
and the ambiguous-target refusal at `build()`. Real Ctrl-C/SIGINT delivery is `asyncio.Runner`'s
own job (Python 3.12+), verified at the process boundary by `test_cli.py`'s subprocess tests,
not re-proven here.
"""

from __future__ import annotations

import asyncio
import io
import threading

import pytest

from agentdeck import RunStatus, WorkflowCtx, workflow
from agentdeck.authoring import Agent
from agentdeck.bindings import DeckGateway
from agentdeck.bindings.terminal import Terminal
from agentdeck.deck import Deck
from agentdeck.errors import ConfigError


async def _survey(ctx: WorkflowCtx, topic: str) -> str:
    answer = await ctx.ask(f"pick a color for {topic}?", options=["red", "blue"])
    return f"{topic}:{answer}"


async def _pending(ctx: WorkflowCtx, _: str) -> str:
    return await ctx.ask("proceed?")


class _BlockingStdin:
    """Yields ``lines`` in order, then blocks past the point a test cancels the loop, so the run
    stays in flight. The binding's reader thread is a daemon and the loop only awaits a queue, so
    a blocked read holds nothing up; :meth:`release` and the timeout keep the test itself tidy.
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


def test_terminal_stdio_declares_a_stdio_surface():
    binding = Terminal.stdio(target="Greeter")
    assert binding.info.kind == "surface"
    assert binding.info.transport == "stdio"
    assert binding.info.advertises == {"streaming", "text", "hitl", "control.cancel"}


async def test_a_turn_an_interrupt_and_an_answer_reach_completion_matching_the_snapshot():
    deck = Deck(workflows=[workflow(_survey, name="Survey")])
    gateway = DeckGateway(deck)
    stdout = io.StringIO()
    binding = Terminal.stdio(target="Survey", stdin=io.StringIO("kites\n2\n"), stdout=stdout)
    endpoint = binding.build(gateway)

    async with deck:
        await endpoint.run()

    assert stdout.getvalue() == ("> ? pick a color for kites?\n  1) red\n  2) blue\n> -- run.completed --\n> ")


async def test_an_out_of_range_choice_is_refused_and_re_prompts_instead_of_raising():
    """`run.answer` raises `ValueError` for a choice outside the declared options: the one path
    `_answer` exists to catch, so a fat-fingered number cannot crash the whole session.
    """
    deck = Deck(workflows=[workflow(_survey, name="Survey")])
    gateway = DeckGateway(deck)
    stdout = io.StringIO()
    binding = Terminal.stdio(target="Survey", stdin=io.StringIO("kites\n9\n2\n"), stdout=stdout)
    endpoint = binding.build(gateway)

    async with deck:
        await endpoint.run()

    transcript = stdout.getvalue()
    assert "! this run is waiting for one of" in transcript
    assert transcript.endswith("-- run.completed --\n> ")


async def test_an_unknown_target_is_refused_at_build_not_at_the_first_prompt():
    """`build()` runs inside `expose()`, so a typo fails before anyone is prompted."""
    deck = Deck(agents=[Agent(name="Greeter", instructions=".")])
    binding = Terminal.stdio(target="Greeeter")

    with pytest.raises(ConfigError) as excinfo:
        binding.build(DeckGateway(deck))

    assert "Greeeter" in str(excinfo.value)
    assert "Greeter" in str(excinfo.value)


async def test_a_workflow_is_a_target_like_any_agent():
    """Both kinds are targets: the old auto-resolution looked at agents only, which left a
    workflow-only deck with no way to name one."""
    deck = Deck(workflows=[workflow(_survey, name="Survey")])
    binding = Terminal.stdio()

    binding.build(DeckGateway(deck))  # exactly one target, and it is a workflow


async def test_two_targets_and_no_target_argument_raises_config_error_naming_both():
    deck = Deck(
        agents=[
            Agent(name="Alpha", instructions="."),
            Agent(name="Bravo", instructions="."),
        ]
    )
    gateway = DeckGateway(deck)
    binding = Terminal.stdio()

    with pytest.raises(ConfigError) as excinfo:
        binding.build(gateway)

    assert "Alpha" in str(excinfo.value)
    assert "Bravo" in str(excinfo.value)


async def test_many_interrupts_cost_no_stack():
    """Driving is iterative: the old shape recursed through `_answer` into `_drive` per ask, so a
    workflow with many asks grew the Python stack until it broke.
    """

    async def _twenty(ctx: WorkflowCtx, _: str) -> str:
        for index in range(20):
            await ctx.ask(f"question {index}?", options=["yes"])
        return "done"

    deck = Deck(workflows=[workflow(_twenty, name="Twenty")])
    stdout = io.StringIO()
    script = "go\n" + "1\n" * 20
    binding = Terminal.stdio(target="Twenty", stdin=io.StringIO(script), stdout=stdout)
    endpoint = binding.build(DeckGateway(deck))

    async with deck:
        await endpoint.run()

    assert stdout.getvalue().count("? question ") == 20
    assert "-- run.completed --" in stdout.getvalue()


async def test_cancelling_mid_run_records_the_cancel_then_re_raises():
    """The application logic this binding owns: `asyncio.Runner` turning a real SIGINT into a
    `CancelledError` here is Python's own job, not re-proven by this test  -  `task.cancel()`
    is the same delivery a real Ctrl-C gives this coroutine either way.
    """
    deck = Deck(workflows=[workflow(_pending, name="Pending")])
    gateway = DeckGateway(deck)
    stdin = _BlockingStdin("go\n")
    binding = Terminal.stdio(target="Pending", stdin=stdin, stdout=io.StringIO())

    async with deck:
        endpoint = binding.build(gateway)
        task = asyncio.ensure_future(endpoint.run())
        try:
            await _wait_until(lambda: _is_waiting(gateway))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            runs = await gateway.list_runs()
            assert await runs[0].status() == RunStatus.CANCELLED
        finally:
            stdin.release()


async def _is_waiting(gateway: DeckGateway) -> bool:
    runs = await gateway.list_runs()
    return bool(runs) and await runs[0].status() == RunStatus.WAITING_ANSWER
