"""One server process for the multi-process concurrency suite.

Started as ``python -u tests/concurrency_worker.py <race> <tag> <trials> <dir>``. Each
worker builds its own store, control port, engine and Runtime over the sqlite files in
``<dir>``, so the two peers share no Python object at all  -  the file is the only thing
they agree through, which is what makes the races in ``test_multiprocess_concurrency.py``
races between servers rather than between two tasks.

Peers synchronize through files, never sleeps: a worker announces it has arrived at a
barrier and waits until the other has, so both leave together. Each trial prints one
``<trial> <tag> <kind> ...`` line; that line is how the test learns what this process saw
without having to interleave reads with a live process.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, Model, SQLiteSession
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.executors.openai_agents import ExecutionStore, OpenAIAgentsExecutor
from agentdeck.adapters.executors.stub import StubExecutor, stub_spec
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.core.content import TextBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import Signal
from agentdeck.core.events import (
    Custom,
    Event,
    MessageCompleted,
    RunCompleted,
    RunInterrupted,
    RunStarted,
    TextDelta,
    Usage,
)
from agentdeck.core.invocable import InvocableKind, InvocableSpec
from agentdeck.errors import SessionBusyError
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence
    from datetime import timedelta

    from agentdeck.core.content import Input
    from agentdeck.core.events import KnownPayload, RunResumed
    from agentdeck.core.ports import SessionClaim

from agentdeck.core.status import Play, continuation_of

TENANT = "demo"
PRINCIPAL = "user:demo"
PEER = {"a": "b", "b": "a"}
THREAD_ID = "thread-1"

# A trial that outlives this is wedged, not slow: raising here exits the process non-zero,
# which the test reads as a failure instead of waiting out its own subprocess timeout.
TRIAL_TIMEOUT = 60.0

APPROVER = "Approver"
CHATTY = "Chatty"
CHATTY_DELTAS = 15
CHUNK_COUNT = 6

RESTART_LOG = "restart-log"
RESTART_SUSPENDED = "restart-suspended"
RESTART_KILLED = "restart-killed"
RESTART_STALL_AFTER = 3

TAKEOVER_LOG = "takeover-log"
TAKEOVER_KILLED = "takeover-killed"
TAKEOVER_NEXT = "takeover-next"
TAKEOVER_STALL_AFTER = 3
REFUSED = "refused"

APPROVED_KINDS = ["run.resumed", "custom", "message.completed", "run.completed"]


def events_db(root: Path) -> Path:
    return root / "events.sqlite3"


def session_db(root: Path) -> Path:
    return root / "session.sqlite3"


def control_db(root: Path) -> Path:
    return root / "control.sqlite3"


def marks_file(root: Path) -> Path:
    return root / "node-b-marks"


def windows_file(root: Path) -> Path:
    return root / "claim-windows"


def resume_session(trial: int) -> str:
    return f"resume-log-{trial}"


def resume_run_id(trial: int) -> str:
    return f"resume-{trial}"


def cancel_run_id(trial: int) -> str:
    return f"cancel-{trial}"


def cancel_is_racing(trial: int) -> bool:
    """Whether this trial lets the timing pick the winner, or orders completion ahead of
    the signal so that side of the invariant is checked too."""
    return trial % 2 == 0


def crossrun_session(trial: int) -> str:
    return f"crossrun-{trial}"


def crossrun_run_id(trial: int, tag: str) -> str:
    return f"crossrun-{trial}-{tag}"


def crossrun_script(tag: str) -> list[KnownPayload]:
    """One whole run, opening and terminal included, long enough that two of them appended at
    once really do interleave in the file rather than landing as two tidy blocks."""
    return [
        RunStarted(
            invocable=CHATTY,
            kind_of_invocable="agent",
            input=[TextBlock(text="go")],
        ),
        *(TextDelta(message_id=f"m-{tag}", text=f"{tag}{index} ") for index in range(CHATTY_DELTAS)),
        MessageCompleted(message_id=f"m-{tag}", text=f"{tag} done"),
        RunCompleted(output=[TextBlock(text=f"{tag} done")], usage=Usage(input_tokens=1, output_tokens=1)),
    ]


def session_name(trial: int) -> str:
    return f"session-{trial}"


def attempted_file(sync: Path, key: str, tag: str) -> Path:
    """Marks that this peer's claim on ``key`` has been answered, win or refusal."""
    return sync / f"attempted.{key}.{tag}"


def runid_file(sync: Path, name: str) -> Path:
    """Where a process that started a run under ``name`` writes the run's real, minted id, so
    another process that could not have predicted it (a peer, or the test once a trial has
    settled) can learn it instead of guessing.

    ``name`` is one of this file's own per-trial or per-scenario constants
    (``resume_run_id(trial)``, ``TAKEOVER_KILLED``, ...): a caller-chosen value that used to
    double as the run's own id and now only serves as a label to synchronize on.
    """
    return sync / f"runid.{name}"


def session_attempt_runid_file(sync: Path, session_id: str, tag: str) -> Path:
    """One peer's real id for its own attempt at ``session_id``'s claim, written the instant both
    peers leave the claim barrier, so whichever loses the claim, or the test once the trial has
    settled, can learn the winner's id without having been able to predict it."""
    return runid_file(sync, f"{session_id}.{tag}")


def session_key(trial: int) -> str:
    """The SDK session key the engine keeps this session's execution state under."""
    return f"{TENANT}:{session_name(trial)}"


def turn_input(tag: str) -> str:
    """Each peer's turn says who asked it, so the engine's own session shows whose turn ran."""
    return f"go {tag}"


def session_items(root: Path, trial: int) -> list[Any]:
    """One trial's engine-private execution state, read from the file both peers shared."""
    session = SQLiteSession(session_key(trial), session_db(root))
    try:
        return asyncio.run(session.get_items())
    finally:
        session.close()


def context(run_id: str, session_id: str | None = None) -> RunContext:
    return RunContext(namespace=TENANT, run_id=run_id, session_id=session_id)


def _report(trial: int, tag: str, kinds: Sequence[str]) -> None:
    print(trial, tag, *kinds, flush=True)


async def _meet(sync: Path, name: str, tag: str, interval: float) -> None:
    (sync / f"{name}.{tag}").touch()
    peer = sync / f"{name}.{PEER[tag]}"
    while not peer.exists():
        await asyncio.sleep(interval)


async def _barrier(sync: Path, name: str, tag: str) -> None:
    """Leave the barrier at the same instant the peer does.

    Two stages on purpose. The first absorbs however long the peer needs to get here, at a
    polling interval that costs nothing; the second is entered only once both are known to
    be present, so its tight spin is short  -  and neither side's polling interval gets to
    decide who wins the race that follows.
    """
    await _meet(sync, f"{name}.arrive", tag, 0.002)
    await _meet(sync, f"{name}.leave", tag, 0.0)


async def _await_file(path: Path) -> None:
    while not path.exists():
        await asyncio.sleep(0.002)


def approver_spec() -> InvocableSpec:
    """Interrupts once, then plays node B. Node B is an event of its own, so a second
    execution of it would show up in the log rather than having to be inferred."""
    return stub_spec(
        APPROVER,
        TextDelta(message_id="m1", text="checking "),
        RunInterrupted(interrupt_id="i1", reason="approval", payload={"question": "approve?"}, thread_id=THREAD_ID),
        Custom(name="node.b", data={"decision": "approved"}),
        MessageCompleted(message_id="m1", text="approved"),
        RunCompleted(output=[TextBlock(text="approved")], usage=Usage(input_tokens=1, output_tokens=1)),
        kind=InvocableKind.WORKFLOW,
    )


def chatty_spec(tag: str) -> InvocableSpec:
    """Enough events per run that two runs writing at once genuinely interleave in the file
    instead of landing as two tidy blocks."""
    return stub_spec(
        CHATTY,
        *(TextDelta(message_id=f"m-{tag}", text=f"{tag}{index} ") for index in range(CHATTY_DELTAS)),
        MessageCompleted(message_id=f"m-{tag}", text=f"{tag} done"),
        RunCompleted(output=[TextBlock(text=f"{tag} done")], usage=Usage(input_tokens=1, output_tokens=1)),
    )


class MarkingStub(StubExecutor):
    """Appends a line every time an answered play is entered, so "node B ran exactly once" is a
    fact about which process reached the executor and not only about what the log ended up
    holding.

    Also refuses to yield its last (terminal) event until the peer's ``claim_resume`` has been
    answered  -  the resume race's version of ``PeerClaimModel``, at the engine boundary rather
    than the model's, so the winner's run cannot finish before the loser has asked.
    """

    def __init__(self, marks: Path, sync: Path, tag: str) -> None:
        self._marks = marks
        self._sync = sync
        self._tag = tag

    async def execute(
        self, spec: InvocableSpec, input: Input, history: Sequence[Event], ctx: RunContext
    ) -> AsyncGenerator[KnownPayload, None]:
        if continuation_of(history, ctx.run_id).play is not Play.ANSWER:
            async for payload in super().execute(spec, input, history, ctx):
                yield payload
            return
        with self._marks.open("a") as handle:
            handle.write(f"{ctx.run_id}\n")
        pending: KnownPayload | None = None
        async for payload in super().execute(spec, input, history, ctx):
            if pending is not None:
                yield pending
            pending = payload
        if pending is not None:
            await _await_file(attempted_file(self._sync, ctx.run_id, PEER[self._tag]))
            yield pending


class ClaimTimingStore(SqliteEventStore):
    """Records the window each ``claim_resume`` attempt occupied, holding both peers at a
    barrier in the one instant before the claim itself.

    The barrier belongs *here* rather than before the ``resume`` call, for the same reason
    ``ClaimStartTimingStore`` puts its own barrier inside ``claim_start``: released earlier, the
    two peers drift apart over whatever runs ahead of the store, and on a loaded box the
    winner's whole run can be over before the loser asks  -  a legal pair of sequential resumes
    that proves nothing about the claim. Meeting at the claim itself, the loser always asks
    while the winner's claim is in flight.

    The window is still recorded, because a barrier says the peers arrived together and only
    the two windows say they were genuinely inside the claim at the same moment. Wall-clock
    nanoseconds, not a monotonic count: only the wall clock means the same thing in two
    processes.
    """

    def __init__(self, path: Path, windows: Path, sync: Path, tag: str) -> None:
        super().__init__(path)
        self._windows = windows
        self._sync = sync
        self._tag = tag

    async def claim_resume(self, resumed: RunResumed, ctx: RunContext, origin: str) -> Event | None:
        run_id = ctx.run_id
        await _barrier(self._sync, f"resumeclaim.{run_id}", self._tag)
        started = time.time_ns()
        try:
            return await super().claim_resume(resumed, ctx, origin)
        finally:
            with self._windows.open("a") as handle:
                handle.write(f"{run_id} {started} {time.time_ns()}\n")
            # Whatever it answered, this peer has now asked  -  which is what the winner's own
            # run waits for before it is allowed to finish.
            attempted_file(self._sync, run_id, self._tag).touch()


class ClaimStartTimingStore(SqliteEventStore):
    """``ClaimTimingStore``'s twin for the session claim: it holds each peer at a barrier in the
    one instant before the claim, and records the window the attempt then occupied.

    The barrier belongs *here* rather than before the turn, because "exactly one turn ran" has to
    be a fact about the claim and not about who was faster. Released earlier, the two peers drift
    apart over a store read apiece  -  and on a loaded box the winner's whole run can be over before
    the loser asks, which is a legal pair of sequential turns and proves nothing about the claim.
    Meeting at the claim itself, the loser always asks while the winner's claim is in flight.

    The window is still recorded, because a barrier says the peers arrived together and only the
    two windows say they were genuinely inside the claim at the same moment. Wall-clock
    nanoseconds, not a monotonic count: only the wall clock means the same thing in two processes.
    """

    def __init__(self, path: Path, windows: Path, sync: Path, tag: str) -> None:
        super().__init__(path)
        self._windows = windows
        self._sync = sync
        self._tag = tag

    async def claim_start(
        self,
        opening: RunStarted,
        ctx: RunContext,
        origin: str,
        stale_after: timedelta,
        *,
        dead: frozenset[str] = frozenset(),
    ) -> tuple[SessionClaim, Event | None]:
        session_id = ctx.session_id or ctx.run_id
        await _barrier(self._sync, f"claim.{session_id}", self._tag)
        # This peer's own real id, regardless of whether its claim goes on to win or lose: the
        # loser needs the winner's id to check what the refusal named, and neither peer can
        # predict the other's minted id ahead of time.
        session_attempt_runid_file(self._sync, session_id, self._tag).write_text(ctx.run_id)
        started = time.time_ns()
        try:
            return await super().claim_start(opening, ctx, origin, stale_after, dead=dead)
        finally:
            with self._windows.open("a") as handle:
                handle.write(f"{session_id} {started} {time.time_ns()}\n")
            # Whatever it answered, this peer has now asked  -  which is what the winner's own run
            # waits for before it is allowed to finish.
            attempted_file(self._sync, session_id, self._tag).touch()


class FileSessions(ExecutionStore):
    """The engine's execution state in a file, so what a turn wrote into the SDK's session
    outlives the process that wrote it and both peers really share one conversation."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._open: dict[str, SQLiteSession] = {}

    def session_for(self, ctx: RunContext) -> SQLiteSession:
        key = f"{ctx.namespace}:{ctx.session_id}"
        session = self._open.get(key)
        if session is None:
            session = self._open[key] = SQLiteSession(key, self._path)
        return session


class StallingStore(SqliteEventStore):
    """Blocks forever once a run this store has not been told to ``ignore`` reaches ``after``
    durable events, so the test can kill this process with that run genuinely open mid-stream,
    not tidily between two runs.

    A run's minted id can no longer be predicted before it opens, so a caller that has already
    opened one run under this store and captured its real id off that run's own first event calls
    :meth:`ignore` before opening a second. Doing so also wipes out whatever count that first
    run's own opening event already left behind, so the second run still stalls at its own
    ``after``th event rather than one event early.
    """

    def __init__(self, path: Path, after: int, mid: Path) -> None:
        super().__init__(path)
        self._after = after
        self._mid = mid
        self._written = 0
        self._ignored: set[str] = set()

    def ignore(self, run_id: str) -> None:
        self._ignored.add(run_id)
        self._written = 0

    async def append(self, payloads: Sequence[KnownPayload], ctx: RunContext, origin: str) -> list[Event]:
        written = await super().append(payloads, ctx, origin)
        await self._stall_once_deep_enough(written)
        return written

    async def claim_start(
        self,
        opening: RunStarted,
        ctx: RunContext,
        origin: str,
        stale_after: timedelta,
        *,
        dead: frozenset[str] = frozenset(),
    ) -> tuple[SessionClaim, Event | None]:
        # A run's opening event is written by the session claim, not by append, so counting a
        # run's durable events means counting at both doors.
        claim, event = await super().claim_start(opening, ctx, origin, stale_after, dead=dead)
        if event is not None:
            await self._stall_once_deep_enough([event])
        return claim, event

    async def _stall_once_deep_enough(self, events: Sequence[Event]) -> None:
        self._written += sum(1 for event in events if event.run_id not in self._ignored)
        if self._written >= self._after:
            self._mid.touch()
            while True:  # a SIGKILL from the test is the only way out, which is the point
                await asyncio.sleep(0.05)


class PeerClaimModel(Model):
    """Streams a fixed script but refuses to finish until the peer's claim has been answered.

    Without it, "exactly one turn ran" is only true when the loser asks while the winner is still
    running, and nothing guarantees that: on a box with one usable core the winner's whole run can
    be over before the loser is scheduled at all, which is a legal pair of *sequential* turns and
    says nothing about the claim. Waiting for the peer's mark makes the overlap a property of the
    fixture instead of a hope about the scheduler.
    """

    def __init__(self, attempted: Path) -> None:
        self._attempted = attempted

    async def stream_response(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for index in range(CHUNK_COUNT):
            yield _delta(index)
        await _await_file(self._attempted)
        yield _completed()

    async def get_response(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("this fixture only streams")


class BarrierModel(Model):
    """Streams a fixed script, stopping at a barrier with ``remaining`` chunks left so the
    cancel signal is released into a run with a known, tiny amount of work still to do.

    ``remaining=None`` streams straight through and never meets the signaller  -  that is the
    ordering where completion has already won before a signal is even written. Varying it
    over the racing trials changes how much the run has left when the signal lands: none
    means it is at its last safe point, two means two more events and two more checkpoints.
    """

    def __init__(self, sync: Path, name: str, tag: str, remaining: int | None) -> None:
        self._sync = sync
        self._name = name
        self._tag = tag
        self._remaining = remaining

    async def stream_response(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        before = CHUNK_COUNT if self._remaining is None else CHUNK_COUNT - self._remaining
        for index in range(before):
            yield _delta(index)
        if self._remaining is not None:
            await _barrier(self._sync, self._name, self._tag)
        for index in range(before, CHUNK_COUNT):
            yield _delta(index)
        yield _completed()

    async def get_response(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("this fixture only streams")


def _delta(index: int) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent(
        content_index=0,
        delta=f"chunk{index} ",
        item_id="msg-racer",
        logprobs=[],
        output_index=0,
        sequence_number=0,
        type="response.output_text.delta",
    )


def chunk_text() -> str:
    """The whole answer ``BarrierModel`` streams  -  what one finished turn leaves in a session."""
    return "".join(f"chunk{index} " for index in range(CHUNK_COUNT))


def _completed() -> ResponseCompletedEvent:
    text = chunk_text()
    usage = ResponseUsage(
        input_tokens=1,
        input_tokens_details=InputTokensDetails(cached_tokens=0),
        output_tokens=1,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
        total_tokens=2,
    )
    message = ResponseOutputMessage(
        id="msg-racer",
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )
    response = Response(
        id="resp-racer",
        created_at=0.0,
        model="fake-racer",
        object="response",
        output=[message],
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        usage=usage,
    )
    return ResponseCompletedEvent(response=response, sequence_number=0, type="response.completed")


async def _race_resume(tag: str, trials: int, root: Path) -> None:
    """Both peers answer one interrupt at the same instant, meeting inside the claim itself; the
    store picks the winner. The barrier lives in ``ClaimTimingStore`` for the reason its
    docstring gives, and ``MarkingStub`` holds the winner's run open until the loser's claim has
    been answered  -  the same shape ``ClaimStartTimingStore``/``PeerClaimModel`` use for the
    session race, moved from the model boundary to the engine's.
    """
    sync = root / "sync"
    store = ClaimTimingStore(events_db(root), windows_file(root), sync, tag)
    runtime = Runtime([MarkingStub(marks_file(root), sync, tag)], store, {APPROVER: approver_spec()})
    for trial in range(trials):
        async with asyncio.timeout(TRIAL_TIMEOUT):
            session_id = resume_session(trial)
            opened = sync / f"opened.{trial}"
            runid = runid_file(sync, resume_run_id(trial))
            if tag == "a":
                opening = [
                    event
                    async for event in runtime.run(
                        APPROVER,
                        coerce_input("go"),
                        session_id=session_id,
                        namespace=TENANT,
                    )
                ]
                assert opening[-1].kind == "run.interrupted", [event.kind for event in opening]
                # The run's real id, minted inside ``run()`` and unknowable ahead of time. The
                # other peer never opened this run, so it has no other way to learn it.
                runid.write_text(opening[0].run_id)
                opened.touch()
            else:
                await _await_file(opened)
            real_id = runid.read_text()
            resumed = [
                event
                async for event in runtime.resume(
                    APPROVER,
                    "approved",
                    run_id=real_id,
                    session_id=session_id,
                    namespace=TENANT,
                )
            ]
            _report(trial, tag, [event.kind for event in resumed])


async def _race_cancel(tag: str, trials: int, root: Path) -> None:
    """One peer streams a run to its end while the other signals CANCEL against it.

    Alternating trials, because a photo finish only ever lands on one side of itself. Even
    trials release both peers from one barrier and let the timing decide. Odd trials order
    it the other way round on purpose  -  the run finishes first and only then is the signal
    written  -  which is the one outcome a coin toss cannot be relied on to produce, and the
    half of "whichever wins, nothing follows it" that would otherwise go unexercised.
    """
    sync = root / "sync"
    control = SqliteControlPort(control_db(root))
    store = SqliteEventStore(events_db(root))
    for trial in range(trials):
        async with asyncio.timeout(TRIAL_TIMEOUT):
            barrier_name = cancel_run_id(trial)
            finished = sync / f"finished.{trial}"
            runid = runid_file(sync, cancel_run_id(trial))
            if tag == "b":
                if cancel_is_racing(trial):
                    await _barrier(sync, barrier_name, tag)
                else:
                    await _await_file(finished)
                # The real id is minted inside "a"'s ``run()`` call, which this peer never made.
                # It is written to disk the moment "a" sees its own first event, well before
                # this barrier can be reached.
                await _await_file(runid)
                await control.signal(runid.read_text(), Signal.CANCEL)
                _report(trial, tag, ["signalled"])
                continue
            remaining = (trial // 2) % 3 if cancel_is_racing(trial) else None
            agent = Agent(name="Racer", instructions="stream", model=BarrierModel(sync, barrier_name, tag, remaining))
            spec = InvocableSpec(
                name="Racer", kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=agent
            )
            runtime = Runtime([OpenAIAgentsExecutor()], store, {"Racer": spec}, control=control)
            events: list[Event] = []
            async for event in runtime.run("Racer", coerce_input("go"), namespace=TENANT):
                if not events:
                    runid.write_text(event.run_id)
                events.append(event)
            finished.touch()
            _report(trial, tag, [event.kind for event in events])


async def _race_session(tag: str, trials: int, root: Path) -> None:
    """Both peers start a turn on one session, meeting inside the claim itself; the store picks
    the winner. The barrier lives in ``ClaimStartTimingStore`` for the reason its docstring gives.

    A real engine with its execution state in a file, not the stub: the reason one turn per
    session is a rule at all is that the loser would otherwise be handing the same SDK session
    to a second run of the same conversation, and only a file shows what the winner left there
    after both processes are gone.
    """
    sync = root / "sync"
    store = ClaimStartTimingStore(events_db(root), windows_file(root), sync, tag)
    engine = OpenAIAgentsExecutor(FileSessions(session_db(root)))
    for trial in range(trials):
        async with asyncio.timeout(TRIAL_TIMEOUT):
            session_id = session_name(trial)
            model = PeerClaimModel(attempted_file(sync, session_id, PEER[tag]))
            agent = Agent(name=CHATTY, instructions="stream", model=model)
            spec = InvocableSpec(
                name=CHATTY, kind=InvocableKind.AGENT, executor=OpenAIAgentsExecutor.name, native=agent
            )
            runtime = Runtime([engine], store, {CHATTY: spec})
            try:
                events = [
                    event
                    async for event in runtime.run(
                        CHATTY,
                        coerce_input(turn_input(tag)),
                        session_id=session_id,
                        namespace=TENANT,
                    )
                ]
            except SessionBusyError as refusal:
                # Asserted here rather than reported outwards: the refusal has to name the
                # session and the run holding it, and a wrong message must fail the trial. The
                # winner's real id was never predictable, so it is read from the file
                # ``ClaimStartTimingStore`` wrote for it the instant both peers left the barrier.
                winner_runid = session_attempt_runid_file(sync, session_id, PEER[tag])
                await _await_file(winner_runid)
                assert session_name(trial) in str(refusal), str(refusal)
                assert winner_runid.read_text() in str(refusal), str(refusal)
                _report(trial, tag, [REFUSED])
                continue
            _report(trial, tag, [event.kind for event in events])


async def _race_crossrun(tag: str, trials: int, root: Path) -> None:
    """Two different runs of one log, written into by both peers at once, event by event.

    Below the Runtime deliberately: one session admits one turn now, so a log with two live runs
    in it is what a takeover leaves behind rather than something a caller can ask for. What the
    store owes both of them is that each run's ``seq`` is its own and every number it assigns is
    a number it persisted  -  which is why each peer asserts here, while the other is still writing
    into the same file, that the log gave it back exactly the seqs it expected.

    The peers no longer stamp their own events, so there is no spent ``seq`` for one of them to
    offer back: the store reads the run's last number under the file's write lock and hands out
    the next one. That makes the duplicate this trial used to demand a refusal for unconstructible
    rather than merely refused, and moves the burden onto what the store *returns*  -  checked here
    per write, and again over the settled file by the trial that spawned these peers.
    """
    sync = root / "sync"
    store = SqliteEventStore(events_db(root))
    for trial in range(trials):
        async with asyncio.timeout(TRIAL_TIMEOUT):
            session_id = crossrun_session(trial)
            ctx = context(crossrun_run_id(trial, tag), session_id)
            script = crossrun_script(tag)
            await _barrier(sync, f"crossrun-{trial}", tag)
            for expected, payload in enumerate(script):
                written = await store.append([payload], ctx, CHATTY)
                _assert_assigned(written, expected, ctx)
            _report(trial, tag, [payload.kind for payload in script])


def _assert_assigned(written: list[Event], expected: int, ctx: RunContext) -> None:
    """One write, one event, at this run's next number and under this run's own name.

    Asserted in the worker rather than only over the settled file, because a store that handed
    this peer a number belonging to the other run  -  or one it had already given away  -  is caught
    here while both peers are still writing, with the trial that spawned them reporting it as a
    failed worker instead of as a merely odd log.
    """
    if [(event.run_id, event.seq) for event in written] != [(ctx.run_id, expected)]:
        raise AssertionError(
            f"log {ctx.session_id!r} gave run {ctx.run_id!r} {[(e.run_id, e.seq) for e in written]}, "
            f"expected [({ctx.run_id!r}, {expected})]"
        )


async def _takeover_victim(root: Path) -> None:
    """Open a turn on the session and stall mid-stream, waiting to be killed with it open."""
    sync = root / "sync"
    store = StallingStore(events_db(root), TAKEOVER_STALL_AFTER, sync / "mid")
    runtime = Runtime([StubExecutor()], store, {CHATTY: chatty_spec("killed")})
    real_id: str | None = None
    async for event in runtime.run(CHATTY, coerce_input("go"), session_id=TAKEOVER_LOG, namespace=TENANT):
        if real_id is None:
            # The run's own minted id, captured off its first event before the store ever gets
            # a chance to stall: the successor learns it from here rather than predicting it.
            real_id = event.run_id
            runid_file(sync, TAKEOVER_KILLED).write_text(real_id)
        # the store stalls this run partway through and never lets it finish


async def _takeover_successor(root: Path) -> None:
    """A fresh process asking for the next turn of the session the killed run still holds.

    The staleness window is left to the settings on purpose: the test runs this twice, once
    with the default  -  which must refuse  -  and once with the window shortened through the
    environment, the way an operator would set it, which must get through. ``Runtime`` itself
    reads no settings, so this worker resolves the configured window explicitly, the same way
    ``build_runtime`` would.
    """
    sync = root / "sync"
    store = SqliteEventStore(events_db(root))
    runtime = Runtime(
        [StubExecutor()], store, {CHATTY: chatty_spec("next")}, stale_run_after=get_settings().runtime.stale_run_after
    )
    try:
        events = [
            event async for event in runtime.run(CHATTY, coerce_input("go"), session_id=TAKEOVER_LOG, namespace=TENANT)
        ]
    except SessionBusyError as refusal:
        # The victim's real id was minted, not chosen, and this process never saw it start. It
        # was left on disk the moment the victim observed its own first event.
        killed_id = runid_file(sync, TAKEOVER_KILLED).read_text()
        assert TAKEOVER_LOG in str(refusal), str(refusal)
        assert killed_id in str(refusal), str(refusal)
        _report(0, "successor", [REFUSED])
        return
    runid_file(sync, TAKEOVER_NEXT).write_text(events[0].run_id)
    _report(0, "successor", [event.kind for event in events])


async def _restart_victim(root: Path) -> None:
    """Suspend one run, then stall a second one mid-stream and wait to be killed."""
    sync = root / "sync"
    store = StallingStore(events_db(root), RESTART_STALL_AFTER, sync / "mid")
    runtime = Runtime([StubExecutor()], store, {APPROVER: approver_spec(), CHATTY: chatty_spec("killed")})
    opening: list[Event] = []
    async for event in runtime.run(APPROVER, coerce_input("go"), session_id=RESTART_LOG, namespace=TENANT):
        if not opening:
            # This run must complete (it only ever reaches ``run.interrupted``, never ``after``
            # events), so its real id is told to the store to ignore before its own events could
            # otherwise be mistaken for the start of the run meant to stall.
            store.ignore(event.run_id)
            runid_file(sync, RESTART_SUSPENDED).write_text(event.run_id)
        opening.append(event)
    _report(0, "victim", [event.kind for event in opening])
    # Its own log, not the suspended run's: that session has an open run, and one session
    # admits one turn at a time, so a second run there would be refused rather than killed.
    real_id: str | None = None
    async for event in runtime.run(CHATTY, coerce_input("go"), namespace=TENANT):
        if real_id is None:
            real_id = event.run_id
            runid_file(sync, RESTART_KILLED).write_text(real_id)
        # the store stalls this run partway through and never lets it finish


async def _restart_successor(root: Path) -> None:
    """A fresh process on the dead one's log: continue what can be continued, no-op on the rest."""
    sync = root / "sync"
    store = SqliteEventStore(events_db(root))
    runtime = Runtime([StubExecutor()], store, {APPROVER: approver_spec(), CHATTY: chatty_spec("killed")})
    # Both real ids were minted by the victim, not chosen, and left on disk for this process
    # (a fresh one that never saw either run open) to read back.
    suspended_id = runid_file(sync, RESTART_SUSPENDED).read_text()
    resumed = [
        event
        async for event in runtime.resume(
            APPROVER,
            "approved",
            run_id=suspended_id,
            session_id=RESTART_LOG,
            namespace=TENANT,
        )
    ]
    _report(0, "successor", [event.kind for event in resumed])
    killed_id = runid_file(sync, RESTART_KILLED).read_text()
    stray = [
        event
        async for event in runtime.resume(
            CHATTY,
            "approved",
            run_id=killed_id,
            namespace=TENANT,
        )
    ]
    _report(1, "successor", [event.kind for event in stray])


async def main() -> None:
    race, tag, trials, root = sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
    if race == "resume":
        await _race_resume(tag, trials, root)
    elif race == "cancel":
        await _race_cancel(tag, trials, root)
    elif race == "session":
        await _race_session(tag, trials, root)
    elif race == "crossrun":
        await _race_crossrun(tag, trials, root)
    elif race == "takeover" and tag == "victim":
        await _takeover_victim(root)
    elif race == "takeover":
        await _takeover_successor(root)
    elif race == "restart" and tag == "victim":
        await _restart_victim(root)
    elif race == "restart":
        await _restart_successor(root)
    else:
        raise SystemExit(f"unknown race {race!r}")


if __name__ == "__main__":
    asyncio.run(main())
