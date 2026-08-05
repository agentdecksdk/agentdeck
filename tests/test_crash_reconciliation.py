"""A crash between the log write and the session write must not cost the model a message.

A turn writes the log first and the engine's session second, so the window between them is
real: the log keeps the question (or the answer), the process dies, and the session never
gets its copy. Left alone, the next turn feeds the model a conversation with a hole in it —
it answers a question it can no longer see the setup for, and nothing anywhere reports a
problem. That silence is why these tests assert on what the scripted model *received*,
never on an internal flag.

Both crashes here are real rather than stubbed: one fails the session write inside a live
turn, one SIGKILLs a second OS process the instant the answer lands in the log. Neither
patches the reconciliation itself.
"""

from __future__ import annotations

import asyncio
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import crash_worker as worker
import pytest
from agents import SQLiteSession

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.events import MessageCompleted, RunStarted
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.items import TResponseInputItem

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event

WORKER = Path(__file__).parent / "crash_worker.py"

# A wedge detector, not a performance budget: the worker's turn takes milliseconds.
WORKER_TIMEOUT = 120.0

QUESTION_3 = "and what did I just ask you?"
QUESTION_4 = "still there?"

FIRST_EXCHANGE = [["user", worker.QUESTION_1], ["assistant", worker.ANSWER_1]]


class SessionWriteDiedError(RuntimeError):
    """What the SDK's session write raises when this test decides the process is dying."""


class _DyingSession(SQLiteSession):
    """A real session whose write can be made never to land — the state a process killed
    between the log append and the session write leaves behind for the next turn."""

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.writes_fail = False

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        if self.writes_fail:
            raise SessionWriteDiedError("the process died before the session write landed")
        await super().add_items(items)


class _CrashingSessions(ExecutionStore):
    """One ``_DyingSession`` for the whole test, so a turn's failed write is still visible
    to the turn after it — the same session a restarted process would reattach to."""

    def __init__(self) -> None:
        super().__init__()
        self.session = _DyingSession("crash-test")

    def session_for(self, ctx: RunContext) -> SQLiteSession:
        return self.session


def _build(model: worker.ScriptedModel, sessions: ExecutionStore) -> tuple[Runtime, SqliteEventStore]:
    store = SqliteEventStore()
    return Runtime([OpenAIAgentsEngine(sessions)], store, {worker.AGENT: worker.spec(model)}), store


async def _play(runtime: Runtime, question: str, run_id: str) -> list[Event]:
    return [event async for event in runtime.run(worker.AGENT, coerce_input(question), worker.context(run_id))]


def _log_transcript(events: Sequence[Event]) -> list[list[str]]:
    """The log's own message-level view: a turn's input, and every completed answer."""
    transcript: list[list[str]] = []
    for event in events:
        if isinstance(event.payload, RunStarted):
            texts = [block.text for block in event.payload.input if isinstance(block, TextBlock)]
            transcript.append(["user", " ".join(texts)])
        elif isinstance(event.payload, MessageCompleted):
            transcript.append(["assistant", event.payload.text])
    return transcript


async def test_an_input_the_session_write_lost_reaches_the_model_on_the_next_turn() -> None:
    """The narrow gap, with the session surviving: turn 1 lands in both stores, turn 2's
    session write dies with the log already holding the question, and turn 3's model input
    must carry that question exactly once, in place."""
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions()
    runtime, store = _build(model, sessions)

    await _play(runtime, worker.QUESTION_1, "turn-1")

    sessions.session.writes_fail = True
    with pytest.raises(SessionWriteDiedError):
        await _play(runtime, worker.QUESTION_2, "turn-2")
    sessions.session.writes_fail = False

    # The gap is real before anything repairs it: the log is one question ahead.
    reader = worker.context("reader")
    logged = _log_transcript(await store.read(worker.SESSION_ID, reader))
    assert logged == [*FIRST_EXCHANGE, ["user", worker.QUESTION_2]]
    assert worker.transcript_of(await sessions.session.get_items()) == FIRST_EXCHANGE
    assert len(model.inputs) == 1, "turn 2 died before the model was called"

    await _play(runtime, QUESTION_3, "turn-3")

    assert worker.transcript_of(model.inputs[-1]) == [
        *FIRST_EXCHANGE,
        ["user", worker.QUESTION_2],
        ["user", QUESTION_3],
    ]

    # And the two stores agree again afterwards, which is the invariant the gap broke.
    repaired = await store.read(worker.SESSION_ID, reader)
    assert worker.transcript_of(await sessions.session.get_items()) == _log_transcript(repaired)

    # A repair is a one-off, not a per-turn habit: the turn after it must see the same
    # history once, not twice.
    await _play(runtime, QUESTION_4, "turn-4")

    assert worker.transcript_of(model.inputs[-1]) == [
        *FIRST_EXCHANGE,
        ["user", worker.QUESTION_2],
        ["user", QUESTION_3],
        ["assistant", worker.ANSWER_1],
        ["user", QUESTION_4],
    ]


async def test_a_session_that_diverged_from_the_log_is_left_alone() -> None:
    """Message-level replay only repairs a session the log's prefix still describes. A
    session holding something the log never recorded is the authority on execution, so the
    next turn must run on it untouched rather than on a guess about its tail."""
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions()
    runtime, _store = _build(model, sessions)

    await _play(runtime, worker.QUESTION_1, "turn-1")
    await sessions.session.add_items([{"role": "user", "content": "typed straight into the session"}])

    await _play(runtime, worker.QUESTION_2, "turn-2")

    assert worker.transcript_of(model.inputs[-1]) == [
        *FIRST_EXCHANGE,
        ["user", "typed straight into the session"],
        ["user", worker.QUESTION_2],
    ]


async def test_a_turn_a_killed_process_never_wrote_to_its_session_survives_the_restart(tmp_path: Path) -> None:
    """The same gap across two real processes. One is SIGKILLed the instant its answer is
    durable in the log — its session dies with it, unwritten — and a fresh process on that
    log must hand its model the whole conversation, not just the new question."""
    victim = subprocess.Popen(
        [sys.executable, "-u", str(WORKER), "victim", str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        await _wait_for(worker.marker_file(tmp_path), victim)
        victim.kill()
        _stdout, stderr = victim.communicate(timeout=WORKER_TIMEOUT)
    finally:
        if victim.poll() is None:
            victim.kill()
    assert victim.returncode == -signal.SIGKILL, f"the victim was not killed mid-turn: {victim.returncode}\n{stderr}"

    successor = subprocess.run(
        [sys.executable, "-u", str(WORKER), "successor", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT,
        check=False,
    )
    assert successor.returncode == 0, successor.stderr

    fed_to_the_model = json.loads(successor.stdout.splitlines()[-1])
    assert fed_to_the_model == [*FIRST_EXCHANGE, ["user", worker.QUESTION_2]], successor.stdout

    store = SqliteEventStore(worker.events_db(tmp_path))
    log = await store.read(worker.SESSION_ID, worker.context("reader"))
    killed = [event.kind for event in log if event.run_id == worker.VICTIM_RUN]
    assert killed[-1] == worker.STALL_KIND, f"the victim died somewhere else: {killed}"
    assert _log_transcript(log) == [
        *FIRST_EXCHANGE,
        ["user", worker.QUESTION_2],
        ["assistant", worker.ANSWER_2],
    ]
    store.close()


async def _wait_for(path: Path, process: subprocess.Popen[str]) -> None:
    """Block until the worker touches ``path``.

    Watching it exit as well as the file: a process that dies on the way to its gate never
    touches anything, and waiting out the full timeout for that turns a plain crash into a
    two-minute mystery.
    """
    async with asyncio.timeout(WORKER_TIMEOUT):
        while not path.exists():
            if process.poll() is not None:
                _stdout, stderr = process.communicate()
                raise AssertionError(f"the worker exited {process.returncode} before {path.name}\n{stderr}")
            await asyncio.sleep(0.02)
