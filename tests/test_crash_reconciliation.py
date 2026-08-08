"""A crash between the log write and the session write must not cost the model a message.

A turn writes the log first and the engine's session second, so the window between them is
real: the log keeps the question (or the answer), the process dies, and the session never
gets its copy. Left alone, the next turn feeds the model a conversation with a hole in it —
it answers a question it can no longer see the setup for, and nothing anywhere reports a
problem. That silence is why these tests assert on what the scripted model *received*,
never on an internal flag.

The crashes are real rather than stubbed: one fails the session write inside a live turn,
one SIGKILLs a second OS process with the log a question ahead of a session file that
outlives it. Neither patches the reconciliation itself. The tests around them pin what must
*not* be replayed — an abandoned turn's question, a session that went its own way — because
those are the ways a repair turns into a corruption.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import crash_worker as worker
import pytest
from agents import SQLiteSession

from agentdeck.adapters.engines.openai_agents import ExecutionStore, OpenAIAgentsEngine
from agentdeck.adapters.engines.openai_agents.reconcile import DIVERGED
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.events import MessageCompleted, RunStarted
from agentdeck.runtime.service import Runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agents.items import TResponseInputItem

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event, KnownPayload

WORKER = Path(__file__).parent / "crash_worker.py"

# A wedge detector, not a performance budget: the worker's turns take milliseconds.
WORKER_TIMEOUT = 120.0

QUESTION_4 = "and what did I just ask you?"
QUESTION_5 = "still there?"

# How long a held read waits for its second reader before giving up — loop turns, not seconds.
_HELD_READ_TURNS = 200

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


class _SlowReadSession(_DyingSession):
    """A session whose read can be made to wait for a second reader before it comes back.

    A local SQLite read is far too quick for two turns to overlap inside it by luck; one over
    a network is not, and that is the shape the race has to be pinned against. Bounded on
    purpose: under a correct lock the second reader can never arrive, and a test must not
    wedge waiting for it.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(session_id)
        self.reads = 0
        self.readers_awaited = 0

    def hold_reads_for_a_second_reader(self) -> None:
        self.reads = 0
        self.readers_awaited = 2

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = await super().get_items(limit)
        self.reads += 1
        for _ in range(_HELD_READ_TURNS):
            if self.reads >= self.readers_awaited:
                break
            await asyncio.sleep(0)
        return items


class _CrashingSessions(ExecutionStore):
    """One session for the whole test, so a turn's failed write is still visible to the turn
    after it — the same session a restarted process would reattach to."""

    def __init__(self, session_id: str = "crash-test", *, slow_reads: bool = False) -> None:
        super().__init__()
        self.session = _SlowReadSession(session_id) if slow_reads else _DyingSession(session_id)

    def session_for(self, ctx: RunContext) -> SQLiteSession:
        return self.session


def _build(model: worker.ScriptedModel, sessions: ExecutionStore) -> tuple[Runtime, SqliteEventStore]:
    store = SqliteEventStore()
    return Runtime([OpenAIAgentsEngine(sessions)], store, {worker.AGENT: worker.spec(model)}), store


async def _play(runtime: Runtime, question: str, run_id: str) -> list[Event]:
    return [
        event
        async for event in runtime.run(
            worker.AGENT,
            coerce_input(question),
            run_id=(worker.context(run_id)).run_id,
            session_id=(worker.context(run_id)).session_id,
            namespace=(worker.context(run_id)).namespace,
        )
    ]


async def _drain(payloads: AsyncIterator[KnownPayload]) -> list[KnownPayload]:
    """One turn straight off the engine, envelope-free: what a Runtime would have stamped."""
    return [payload async for payload in payloads]


def _log_transcript(events: Sequence[Event]) -> list[list[str]]:
    """The log's own message-level view: a turn's input, and every completed answer."""
    transcript: list[list[str]] = []
    for event in events:
        if isinstance(event.payload, RunStarted):
            texts = [block.text for block in event.payload.input if isinstance(block, TextBlock)]
            transcript.append(["user", "\n".join(texts)])
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

    await _play(runtime, worker.QUESTION_3, "turn-3")

    assert worker.transcript_of(model.inputs[-1]) == [
        *FIRST_EXCHANGE,
        ["user", worker.QUESTION_2],
        ["user", worker.QUESTION_3],
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
        ["user", worker.QUESTION_3],
        ["assistant", worker.ANSWER_1],
        ["user", QUESTION_4],
    ]


async def test_a_question_the_consumer_abandoned_is_not_replayed_in_front_of_its_retry() -> None:
    """``run.started`` records that a turn was asked for, not that the engine took it. A
    consumer that walks away before the engine reads anything leaves a question in the log
    the session never saw — and the user then asks it again. Replaying it would put the
    question in front of its own retry, which is a duplicate, not a repair."""
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions()
    runtime, store = _build(model, sessions)

    await _play(runtime, worker.QUESTION_1, "turn-1")

    # An SSE consumer disconnecting: read the opening event, stop reading, close the stream.
    stream = runtime.run(
        worker.AGENT,
        coerce_input(worker.QUESTION_2),
        run_id=(worker.context("abandoned")).run_id,
        session_id=(worker.context("abandoned")).session_id,
        namespace=(worker.context("abandoned")).namespace,
    )
    assert (await anext(stream)).kind == "run.started"
    await stream.aclose()

    abandoned = [event.kind for event in await store.read(worker.SESSION_ID, worker.context("reader"))]
    assert abandoned[-1] == "run.cancelled", abandoned

    await _play(runtime, worker.QUESTION_2, "retry")

    assert worker.transcript_of(model.inputs[-1]) == [*FIRST_EXCHANGE, ["user", worker.QUESTION_2]]


async def test_a_turn_cancelled_after_its_answer_keeps_both_of_its_messages() -> None:
    """The other half of that rule, and the one that is easy to get wrong. A consumer that
    disconnects *after* the answer leaves a turn the SDK has already persisted whole: input and
    output are both in the session. Dropping its input as "never taken" would misalign the two
    transcripts from that message on — every later turn would report a false divergence and,
    worse, refuse to repair anything, so a real gap after it would never be filled.
    """
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions()
    runtime, store = _build(model, sessions)

    await _play(runtime, worker.QUESTION_1, "turn-1")

    # Stop reading the moment the answer is in the log, then wait for the SDK's own write of
    # that turn to land before closing: the shape under test is a turn the session *has*.
    stream = runtime.run(
        worker.AGENT,
        coerce_input(worker.QUESTION_2),
        run_id=(worker.context("stopped")).run_id,
        session_id=(worker.context("stopped")).session_id,
        namespace=(worker.context("stopped")).namespace,
    )
    async with asyncio.timeout(WORKER_TIMEOUT):
        while (await anext(stream)).kind != "message.completed":
            pass
        while len(worker.transcript_of(await sessions.session.get_items())) < 4:
            await asyncio.sleep(0.01)
    await stream.aclose()

    kinds = [event.kind for event in await store.read(worker.SESSION_ID, worker.context("reader"))]
    assert kinds[-1] == "run.cancelled", kinds
    second_turn = [*FIRST_EXCHANGE, ["user", worker.QUESTION_2], ["assistant", worker.ANSWER_1]]
    assert worker.transcript_of(await sessions.session.get_items()) == second_turn

    # A real gap after it, of the kind this whole module exists for.
    sessions.session.writes_fail = True
    with pytest.raises(SessionWriteDiedError):
        await _play(runtime, worker.QUESTION_3, "crashed")
    sessions.session.writes_fail = False

    events = await _play(runtime, QUESTION_4, "after")

    assert [event.kind for event in events if event.kind == "custom"] == [], "nothing here is a divergence"
    assert worker.transcript_of(model.inputs[-1]) == [
        *second_turn,
        ["user", worker.QUESTION_3],
        ["user", QUESTION_4],
    ]


async def test_a_session_that_diverged_from_the_log_is_left_alone_and_says_so() -> None:
    """Message-level replay only repairs a session the log's prefix still describes. A
    session holding something the log never recorded is the authority on execution, so the
    next turn runs on it untouched — and the disagreement lands in the log as an event, since
    nobody reads warnings."""
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions()
    runtime, store = _build(model, sessions)

    await _play(runtime, worker.QUESTION_1, "turn-1")
    # Rewound and continued somewhere else — a compaction, a manual repair, an SDK session
    # feature this adapter does not know about. What matters is the shape: the log's second
    # message is an answer, the session's is a question, so neither describes the other.
    await sessions.session.pop_item()
    await sessions.session.add_items([{"role": "user", "content": "typed straight into the session"}])

    events = await _play(runtime, worker.QUESTION_2, "turn-2")

    [reported] = [event for event in events if event.kind == "custom" and event.payload.name == DIVERGED]
    assert reported.payload.data == {"agreed_through": 1, "session_messages": 2, "logged_messages": 2}
    assert reported.seq == 1, "the report belongs to the turn it interrupted, right after run.started"
    assert worker.transcript_of(model.inputs[-1]) == [
        ["user", worker.QUESTION_1],
        ["user", "typed straight into the session"],
        ["user", worker.QUESTION_2],
    ]

    stored = await store.read(worker.SESSION_ID, worker.context("reader"))
    assert [event.kind for event in stored if event.kind == "custom"] == ["custom"], "reported once, in the record"


async def test_two_turns_racing_on_one_session_apply_the_repair_once() -> None:
    """Two turns of one session in flight at the same time both find the same gap. Only one
    of them may fill it: read-then-append is not atomic on its own, and the loser would
    otherwise append a second copy of everything the first one repaired.

    Raced at the engine rather than through two ``Runtime.run`` calls, because the Runtime now
    refuses the second of those outright. The overlap is still reachable — a turn that takes a
    silent run's session over runs beside whatever that run is really doing — and this lock is
    then the only thing between the two of them, so it keeps its own test.
    """
    model = worker.ScriptedModel(worker.ANSWER_1)
    sessions = _CrashingSessions("racing-turns", slow_reads=True)
    engine = OpenAIAgentsEngine(sessions)
    spec = worker.spec(model)
    store = SqliteEventStore()
    runtime = Runtime([engine], store, {worker.AGENT: spec})

    await _play(runtime, worker.QUESTION_1, "turn-1")
    sessions.session.writes_fail = True
    with pytest.raises(SessionWriteDiedError):
        await _play(runtime, worker.QUESTION_2, "turn-2")
    sessions.session.writes_fail = False

    history = await store.read(worker.SESSION_ID, worker.context("reader"))
    sessions.session.hold_reads_for_a_second_reader()
    raced = await asyncio.gather(
        _drain(engine.start(spec, coerce_input(worker.QUESTION_3), history, worker.context("racer-a"))),
        _drain(engine.start(spec, coerce_input(QUESTION_5), history, worker.context("racer-b"))),
    )

    session_transcript = worker.transcript_of(await sessions.session.get_items())
    assert session_transcript.count(["user", worker.QUESTION_2]) == 1, session_transcript
    assert session_transcript.count(["user", worker.QUESTION_1]) == 1, session_transcript
    assert [payload.kind for turn in raced for payload in turn].count("custom") == 0


async def test_a_session_lost_entirely_is_refilled_from_the_log() -> None:
    """Execution state that is simply gone — expired, or a first turn that crashed before its
    session write — is the same gap seen from further away, so it takes the same repair: the
    next turn's model input is the log's conversation, not a blank one."""
    first_model = worker.ScriptedModel(worker.ANSWER_1)
    runtime, store = _build(first_model, ExecutionStore())
    await _play(runtime, worker.QUESTION_1, "turn-1")

    # Same log, execution state the process no longer has.
    second_model = worker.ScriptedModel(worker.ANSWER_3)
    restarted = Runtime([OpenAIAgentsEngine(ExecutionStore())], store, {worker.AGENT: worker.spec(second_model)})
    await _play(restarted, worker.QUESTION_2, "turn-2")

    assert worker.transcript_of(second_model.inputs[-1]) == [*FIRST_EXCHANGE, ["user", worker.QUESTION_2]]


async def test_a_turn_a_killed_process_never_wrote_to_its_session_survives_the_restart(tmp_path: Path) -> None:
    """The same gap across two real processes, with execution state in a file so that it
    outlives the one that dies. The victim finishes turn 1 into both stores and is SIGKILLed
    on turn 2's opening log write: the log has that question, the session file does not. A
    fresh process on both must hand its model the whole conversation, gap filled, once.
    """
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

    # The window itself, on disk: the log has the second question, the session stops at the
    # first exchange. Without this the kill could be decorative and the test would not know.
    store = SqliteEventStore(worker.events_db(tmp_path))
    reader = worker.context("reader")
    log = await store.read(worker.SESSION_ID, reader)
    assert _log_transcript(log) == [*FIRST_EXCHANGE, ["user", worker.QUESTION_2]], _kinds(log)
    assert [event.kind for event in log if event.run_id == worker.KILLED_RUN] == ["run.started"], _kinds(log)
    assert worker.transcript_of(await worker.durable_session(tmp_path).get_items()) == FIRST_EXCHANGE

    # The killed turn is still open in the log, and an open run holds its session. A restart
    # takes it over by shortening the staleness window to a millisecond instead of waiting an
    # hour out — which is also the setting itself under test, driven the way an operator sets it.
    successor = subprocess.run(
        [sys.executable, "-u", str(WORKER), "successor", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT,
        check=False,
        env={**os.environ, "AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS": "0.001"},
    )
    assert successor.returncode == 0, successor.stderr
    assert "took it over and closed it as failed" in successor.stderr, successor.stderr

    fed_to_the_model = json.loads(successor.stdout.splitlines()[-1])
    assert fed_to_the_model == [
        *FIRST_EXCHANGE,
        ["user", worker.QUESTION_2],
        ["user", worker.QUESTION_3],
    ], successor.stdout

    after = await store.read(worker.SESSION_ID, reader)
    assert [event.kind for event in after if event.kind == "custom"] == [], "a plain gap is not a divergence"

    # Only the missing question was replayed, not the conversation around it: turn 1's answer is
    # still the SDK's own item, ids and all. A rewrite from the log would have flattened it to
    # plain text, which the transcript above cannot tell apart from a targeted repair.
    items = await worker.durable_session(tmp_path).get_items()
    answers = [item for item in items if item.get("role") == "assistant"]
    assert all(item.get("id") for item in answers), f"an answer was rewritten as plain text: {answers}"
    assert items[2] == {"role": "user", "content": worker.QUESTION_2}, items
    assert len(items) == 5, f"one question was replayed, nothing else: {items}"
    assert worker.transcript_of(await worker.durable_session(tmp_path).get_items()) == _log_transcript(after)
    store.close()


def _kinds(log: Sequence[Event]) -> str:
    return "\n".join(f"  {event.run_id} seq={event.seq} {event.kind}" for event in log)


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
