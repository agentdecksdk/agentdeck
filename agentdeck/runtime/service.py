"""The Runtime: the one place a run is orchestrated.

Per event, in this order: append to the log, fan out to sinks, yield. The order is the
contract — an event a consumer has seen is already persisted, so a consumer that spots a
``seq`` gap can always refetch it.

Engines only yield payloads, and so does this: the store stamps the envelope, assigning
``seq`` and ``ts`` in the same indivisible step that writes the row (ADR-D11). Nothing here
holds a counter, which is what makes the refetch promise above true — a number that cannot be
allocated without being persisted cannot leave a hole behind.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import aclosing, suppress
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError

from agentdeck.core.content import DataBlock, coerce_input
from agentdeck.core.context import RunContext
from agentdeck.core.control import CONTROL_POLL_INTERVAL, Gate, Signal
from agentdeck.core.events import (
    TERMINAL_KINDS,
    ControlRequested,
    Event,
    RunCancelled,
    RunFailed,
    RunInterrupted,
    RunResumed,
    RunStarted,
)
from agentdeck.core.reporting import Reporter
from agentdeck.core.status import (
    PRECONDITIONS,
    SUSPENDED_KINDS,
    Action,
    Operation,
    Ruling,
    RunStatus,
    Verdict,
    can_resume,
    decide,
)
from agentdeck.errors import DOCS_URL, NotFoundError, RunStateError, SessionBusyError, StoreError
from agentdeck.runtime.dispatch import SinkDispatch

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping, Sequence
    from typing import Any

    from agentdeck.core.content import Input
    from agentdeck.core.control import ControlSignal
    from agentdeck.core.events import KnownPayload
    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import ControlPort, EnginePort, EventSinkPort, EventStorePort
    from agentdeck.core.ports.store import RunSummary

logger = logging.getLogger(__name__)

# Mirrors ``RuntimeSettings.stale_run_after_seconds``'s own default (60.0 * 60.0) — duplicated
# rather than imported so a bare ``Runtime()`` needs no settings at all; ``build_runtime`` is
# the caller that resolves the configured value and passes it in.
_DEFAULT_STALE_RUN_AFTER = timedelta(hours=1)
_SESSIONS_DOCS = f"{DOCS_URL}/concepts/sessions-and-memory"


@dataclass(frozen=True, slots=True)
class PendingRun:
    """One run currently ``WAITING_ANSWER`` — what :meth:`Runtime.pending` lists."""

    run_id: str
    session_id: str | None
    invocable: str
    thread_id: str
    payload: dict[str, Any]


class Runtime:
    """Runs invocables and emits one canonical event stream, whatever engine did the work.

    Sinks are optional and buffered — each gets its own bounded queue, so the run is never
    pinned to one. The store stamps every event's ``ts`` in the write that persists it
    (ADR-D11); a caller that wants to hold time injects a clock into the store instead —
    ``MemoryEventStore(clock=...)``, ``RedisEventStore(clock=...)``.

    ``stale_run_after`` is how long a **running** run may go silent before it stops holding its
    session — never a suspended one, which holds until resumed, answered or cancelled.
    ``Runtime`` takes no ambient configuration at all — it defaults to one hour and never reads
    settings itself; ``build_runtime`` is the caller that resolves
    ``AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS`` and passes the configured value in, the same
    as its five peer arguments. ``control_poll_interval`` is how long a run may reuse the
    control answer it already has: it trades cancel latency against the read rate a run costs
    a shared ``ControlPort``, and ``0`` buys the tightest latency at one read per safe point.
    """

    def __init__(
        self,
        engines: Sequence[EnginePort],
        store: EventStorePort,
        invocables: Mapping[str, InvocableSpec],
        sinks: Sequence[EventSinkPort] = (),
        control: ControlPort | None = None,
        stale_run_after: timedelta = _DEFAULT_STALE_RUN_AFTER,
        control_poll_interval: float = CONTROL_POLL_INTERVAL,
    ) -> None:
        self._engines = {engine.engine: engine for engine in engines}
        self._store = store
        self._invocables = invocables
        self._sinks = tuple(SinkDispatch(sink) for sink in sinks)
        self._control = control
        self._stale_run_after = stale_run_after
        self._control_poll_interval = control_poll_interval

    @property
    def store(self) -> EventStorePort:
        """The event log this Runtime plays every run against — the read side of what
        :meth:`run`, :meth:`resume` and :meth:`resume_run` write to, for a caller that wants
        to read a run back directly (e.g. ``App.store``) rather than only watching it live.
        """
        return self._store

    async def run(
        self,
        name: str,
        input: Input,
        *,
        context: object = None,
        session_id: str | None = None,
        namespace: str | None = None,
        key: str | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Play one run of ``name``, yielding every event it produced, ``run.started`` first.

        ``context`` is the application's own value for this run, reaching a callable that declares
        a ``Context[...]`` parameter and nothing else. It is held by reference for the run's whole
        life and never written to the log — the record says what a run was asked to do, not which
        live objects it held.

        ``key`` is the caller's optional stable application identifier — for lookup and
        idempotency, never the run's address. The run's own ``id`` is always minted here, never
        derived from ``key``, so two namespaces reusing one key still get two distinct runs.
        ``(namespace, key)`` is a permanent claim: reusing one whose run already started raises
        ``DuplicateKeyError`` rather than handing back the run that holds it.

        One turn per session at a time: opening the run is a conditional append that fails if
        the session already has one in flight, so a second concurrent turn raises
        ``SessionBusyError`` instead of running against a conversation that is still changing.

        The engine's exception, if any, reaches the caller — but ``run.failed`` is recorded
        first, so the log tells the whole story even when nobody was listening. Every exit
        closes the run in the log: a consumer that walks away gets ``run.cancelled``, whether it
        closed this generator or had its own task cancelled under it.
        """
        spec, engine = self._resolve(name)
        ctx, reports = self._bind(
            self._new_run_context(key=key, session_id=session_id, namespace=namespace, data=context)
        )
        # ponytail: whole log per run — window it (or hand the engine a summary) once a
        # session's history outgrows one read, which a real store will notice long before this does
        history = await self._store.read(ctx.log_key, ctx)

        opening = RunStarted(
            invocable=spec.name,
            kind_of_invocable=spec.kind.value,
            input=input,
        )
        try:
            claimed = await self._claim_session(opening, spec, ctx)
        except asyncio.CancelledError:
            # The claim commits this run before anything is yielded, and it is awaited in the
            # caller's own coroutine — the one an ASGI server cancels when a client disconnects
            # before the response starts. A cancellation landing between the two would leave the
            # run open in the log and its session held for a whole staleness window.
            # Anything not None is a run the claim opened, and it is owed a terminal event.
            if await self._store.run_status(ctx.log_key, ctx.run_id, ctx) is not None:
                await self._close_cancelled(spec, ctx, "cancelled during the claim")
            raise

        async with aclosing(
            self._play(claimed, engine.start(spec, input, history, ctx), spec, ctx, engine, reports)
        ) as run:
            async for event in run:
                yield event

    async def resume(
        self,
        name: str,
        thread_id: str,
        value: Any,
        *,
        context: object = None,
        run_id: str,
        session_id: str | None = None,
        namespace: str | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Continue a run this Runtime suspended earlier.

        ``context`` is resupplied, never recovered: the value is held by reference for one run
        and deliberately never written to the log, so a run picked up here starts with whatever
        this caller hands it. Omitting it is not "keep what the run had" — it is ``None``, and a
        node that read ``ctx.data`` before the interrupt reads ``None`` after it.

        The store's conditional append makes the ``WAITING_ANSWER`` -> ``RUNNING`` transition
        atomic, so exactly one caller wins even when the callers are separate processes; the
        winner opens the run with ``run.resumed``, seq recovered from the log's own
        ``max(seq)`` so it stays contiguous across a process restart — never reset to 0.
        From there the engine plays on exactly like ``run()`` plays an opening: same
        terminal/suspended/exception handling.

        A stray resume — already resumed by a racing caller, or a completed run — is a no-op:
        nothing is read from the engine, nothing is yielded. A run an operator asked to *stop*
        is not stray, and refuses instead: honoring the answer would let it silently override
        somebody who said stop. Both intents survive the refusal — the run is still waiting, and
        the pause is still pending for whoever reads next.

        A **cancel** recorded while the run waited ends it here rather than answering it, for
        the reason :meth:`resume_run` gives: this claim is the only thing that will ever look.
        """
        spec, engine = self._resolve(name)
        ctx, reports = self._bind(
            self._context(run_id=run_id, session_id=session_id, namespace=namespace, data=context)
        )
        status = await self._store.run_status(ctx.log_key, run_id, ctx)
        if status is None:
            return
        # No precondition check here: which states admit an answer is the front door's business
        # (``Deck._answer``), and this method is also where a *loser* lands — a caller whose run
        # was answered out from under it reads ``RUNNING`` and must still no-op, which is what
        # the claim below does for it. Refusing here would turn every lost race into an error.
        #
        # The routing refusal is different and has to be read *before* the claim, which is the
        # one place this path departs from resume_run's order: the claim is the ``run.resumed``
        # carrying the answer, so once it lands the answer cannot be taken back.
        refusal, _ = await self._peek(ctx.id, status)
        if refusal.action is Action.REFUSE:
            raise RunStateError(f"run {run_id!r} cannot be answered: {refusal.why}")
        opening = await self._claim_resume(spec, ctx, value)
        if opening is None:
            return
        ruling, pending = await self._route(ctx.id, status)
        if ruling.action is Action.TERMINATE and pending is not None:
            yield opening
            yield await self._record(ControlRequested(verb="cancel", reason=pending.reason), spec, ctx)
            yield await self._record(RunCancelled(reason=pending.reason), spec, ctx)
            return
        # Any other ruling plays the run on, including a pause that landed inside the window the
        # peek left open: the answer is recorded by now, so the run resumes and meets that pause
        # at its first safe point instead.
        stream = engine.resume(spec, thread_id, value, ctx)
        async with aclosing(self._play(opening, stream, spec, ctx, engine, reports)) as resumed:
            async for event in resumed:
                yield event

    async def resume_run(
        self,
        run_id: str,
        *,
        context: object = None,
        namespace: str | None = None,
        reason: str | None = None,
    ) -> AsyncGenerator[Event, None]:
        """Continue a run that paused at a safe point: same ``run_id``, same log, ``seq``
        counting on from where it stopped.

        ``context`` is resupplied here for the same reason it is on :meth:`resume` — the value
        never reached the log, so the caller lifting the pause is the only one who still has it.

        The engine is re-entered rather than un-suspended, because a paused turn left no stack
        to return to: the log is the checkpoint, so the run is played again from its own
        ``run.started`` input with the log as history. What that means for a caller is stated
        where the safe points are — a step the paused turn already took can be taken twice, so
        a tool with side effects has to tolerate being called again.

        Which states admit a resume is :data:`PRECONDITIONS`' business, not this method's. A
        run that is already running or already over is a no-op — that covers the ordinary races
        without a second answer for a caller to branch on — while one waiting for a *value*
        refuses, naming ``deck.runs.answer``, because silence there reads as "resumed" to a
        caller who is in fact holding the run's only answer.

        A **cancel** recorded while the run was paused is honored here instead of resuming it,
        because this claim is the only thing that will ever look for it: a paused run has no
        loop reaching safe points, so nothing else can turn that request into an effect. The
        run ends ``cancelled`` and is never played on — cancel stays terminal, and asking to
        resume a run somebody cancelled does not quietly override them.
        """
        ctx = self._context(run_id=run_id, namespace=namespace, data=context)
        summary = await self._find(run_id, ctx)
        if summary is None:
            return
        allowed = PRECONDITIONS[summary.status, Operation.RESUME]
        if allowed.verdict is Verdict.REFUSED:
            raise RunStateError(f"run {run_id!r} cannot be resumed: {allowed.why}")
        if allowed.verdict is Verdict.NO_OP:
            return
        started = await self._opening_of(summary.log_key, run_id, ctx)
        if started is None:
            return
        session_id, opened = started
        spec, engine = self._resolve(opened.invocable)
        run_ctx, reports = self._bind(replace(ctx, run_id=run_id, session_id=session_id))
        opening = await self._claim_resume(spec, run_ctx, None, reason)
        if opening is None:
            return
        # Read control only after the claim: the claim is what makes this caller the one actor
        # on the run, so an answer read before it could belong to somebody else's turn.
        ruling, pending = await self._route(run_ctx.id, summary.status)
        if ruling.action is Action.TERMINATE and pending is not None:
            yield opening
            # No ``control.observed``: that event says the run reached a safe point and acted
            # there, and this run reached none — it was already stopped when the cancel landed.
            # The request and the effect are the whole honest story of a cancel served here.
            yield await self._record(ControlRequested(verb="cancel", reason=pending.reason), spec, run_ctx)
            yield await self._record(RunCancelled(reason=pending.reason), spec, run_ctx)
            return
        history = await self._store.read(summary.log_key, run_ctx)
        stream = engine.start(spec, opened.input, history, run_ctx)
        async with aclosing(self._play(opening, stream, spec, run_ctx, engine, reports)) as resumed:
            async for event in resumed:
                yield event

    async def signal(
        self, run_id: str, verb: Signal, reason: str | None = None, *, namespace: str | None = None
    ) -> bool:
        """Record a control request for ``run_id`` in ``namespace``, wherever it is running —
        except a cancel against a run already suspended, which ends it right here instead.

        ``run_id`` here is a run's own minted, globally unique id — the same value its Gate
        polls under (bound in :meth:`_bind`) — so two namespaces can never collide over one:
        each run's id is unrelated to the other's from the moment it was minted.

        ``False`` means this Runtime has no ``ControlPort`` and nothing was recorded — the one
        answer a caller has to act on. Everything else is deliberately not an answer here: the
        run may be inside a tool call, in another process, or already over, and which of those
        it is cannot be known at the moment a caller asks. A signal that loses the race with a
        terminal event is a no-op, since nothing polls the gate once the run loop has exited.

        Not for lifting a pause: a paused run has no loop left to notice anything, so
        :meth:`resume_run` is what continues it (and writes ``RESUME`` itself).

        A **cancel** cannot wait the same way: a suspended run has no loop that will ever poll
        the gate again, so merely recording the signal is betting on somebody else calling
        :meth:`resume`/:meth:`resume_run` later to notice it — which may never happen, wedging
        the very session the cancel was meant to free. :meth:`_cancel_suspended` claims and
        terminates such a run directly. A **pause**, by contrast, stays merely recorded even
        against a suspended run: it has nothing to do until something next resumes or answers
        that run, per the routing table (``docs/design/run-lifecycle.md``).
        """
        if verb is Signal.CANCEL and await self._cancel_suspended(run_id, reason, namespace):
            return True
        if self._control is None:
            logger.warning("no ControlPort is wired: %s for run %s was not recorded", verb.value, run_id)
            return False
        id = self._context(run_id=run_id, namespace=namespace).id
        await self._control.signal(id, verb, reason)
        return True

    async def _cancel_suspended(self, run_id: str, reason: str | None, namespace: str | None) -> bool:
        """Claim ``run_id``'s suspended -> ``RUNNING`` transition and terminate on top of it,
        the same shape :meth:`resume`/:meth:`resume_run` already use when *they* are the ones
        to find a cancel pending. ``False`` for anything not currently suspended (``RUNNING``,
        terminal, or a run ``namespace`` has never heard of) — ``signal`` falls through to
        recording those the ordinary way.

        Losing the claim to a concurrent resume/answer is not an error: that caller is now the
        one actor on the run, and the recorded signal ``signal`` falls through to afterwards is
        exactly what its own routing reads.
        """
        ctx = self._context(run_id=run_id, namespace=namespace)
        summary = await self._find(run_id, ctx)
        if summary is None or not can_resume(summary.status):
            return False
        started = await self._opening_of(summary.log_key, run_id, ctx)
        if started is None:
            return False
        session_id, opened = started
        spec, _ = self._resolve(opened.invocable)
        run_ctx, _ = self._bind(replace(ctx, run_id=run_id, session_id=session_id))
        if await self._claim_resume(spec, run_ctx, None, reason) is None:
            return False
        await self._record(ControlRequested(verb="cancel", reason=reason), spec, run_ctx)
        await self._record(RunCancelled(reason=reason), spec, run_ctx)
        return True

    async def _play(
        self,
        opening: Event,
        stream: AsyncGenerator[KnownPayload, None],
        spec: InvocableSpec,
        ctx: RunContext,
        engine: EnginePort,
        reports: deque[KnownPayload],
    ) -> AsyncGenerator[Event, None]:
        """Yield ``opening``, then everything ``stream`` produces — and close the run in the
        log whichever way it ends.

        One body for all three openings (a start, a resumed interrupt, a lifted pause) because
        every one of them owes the log the same four endings: a terminal event, a suspension, a
        consumer that walked away, or an exception. ``reports`` is drained the same way for all
        three, and for the same reason there is one body at all.
        """
        last = opening.kind
        try:
            yield opening
            async with aclosing(stream) as payloads:
                async for payload in payloads:
                    async for report in self._drain(reports, spec, ctx):
                        yield report
                    yield await self._record(payload, spec, ctx)
                    last = payload.kind
                    if last in TERMINAL_KINDS:
                        # Terminal means terminal: stop reading so nothing can follow it into
                        # the log. An engine yielding more after this gets it discarded.
                        break
        except GeneratorExit:
            # Nobody is listening any more, so there is no event to yield — but an unclosed
            # run in the log is indistinguishable from one still in flight.
            logger.info("run %s abandoned by its consumer after %r", ctx.run_id, last)
            await self._record(RunCancelled(reason="consumer stopped reading"), spec, ctx)
            raise
        except asyncio.CancelledError:
            # The other way a consumer walks away, and the one a real ASGI server delivers: it
            # cancels the task streaming the response rather than closing the generator. This
            # arm exists because ``CancelledError`` is a BaseException, so the one below never
            # saw it and the run stayed open in the log forever.
            logger.info("run %s cancelled after %r", ctx.run_id, last)
            await self._close_cancelled(spec, ctx, "consumer cancelled")
            raise
        except Exception as exc:
            # The exception is the caller's, the event is the record — both, always. The type
            # name only: an exception message can carry content that must not reach a sink.
            logger.exception("run %s failed in engine %r", ctx.run_id, engine.engine)
            yield await self._record(_failed(exc, engine.engine), spec, ctx)
            raise

        if last not in TERMINAL_KINDS and last not in SUSPENDED_KINDS:
            # An engine that just stops leaves consumers waiting forever; close the run for it.
            logger.error("engine %r ended run %s after %r, not a terminal event", engine.engine, ctx.run_id, last)
            yield await self._record(_engine_failed(f"engine {engine.engine!r} ended after {last!r}"), spec, ctx)

    async def _find(self, run_id: str, ctx: RunContext) -> RunSummary | None:
        """Where a run lives and what state it is in. ``None`` means no run of this namespace
        answers to that id.

        Addressed by ``run_id`` within ``ctx``'s namespace — the same value the control plane
        addresses by ``id``, since the two are one field now — so the store's own status
        projection is what locates it: a caller holding a ``run_id`` from a stream it was
        watching has neither the log key nor the invocable's name. Deliberately *unfiltered*:
        narrowing the listing to one status was how a run in every other state came back
        indistinguishable from one that does not exist, which is what let a resume against a
        parked run report nothing at all.
        """
        for summary in await self._store.list_runs(ctx):
            if summary.run_id == run_id:
                return summary
        return None

    async def _opening_of(self, log_key: str, run_id: str, ctx: RunContext) -> tuple[str | None, RunStarted] | None:
        """Whose session this run holds and what it was asked to do — its own ``run.started``.
        Read only once the state machine has admitted the operation, so a refused or no-op call
        never pays for a run's log."""
        for event in await self._store.read_run(log_key, run_id, ctx):
            if isinstance(event.payload, RunStarted):
                return event.session_id, event.payload
        return None

    async def _peek(self, id: str, status: RunStatus) -> tuple[Ruling, ControlSignal | None]:
        """Read the control port and rule on what is there, taking nothing. For the one decision
        that has to be made before a claim — whether the operation is refused at all."""
        pending = None if self._control is None else await self._control.poll(id)
        return decide(status, None if pending is None else pending.verb), pending

    async def _route(self, id: str, status: RunStatus) -> tuple[Ruling, ControlSignal | None]:
        """The one way a stopped run's pending intent is read: poll, decide, and take the intent
        the ruling acted on.

        Taking it is a compare-and-set rather than a clear, so an intent that changed under this
        caller is not destroyed by it. Losing that set means the ruling was made about somebody
        else's signal, so the port is read once more and ruled on again — the second ruling acts
        without taking anything, which leaves whatever is pending now for the gate to meet at the
        run's first safe point.
        """
        ruling, pending = await self._peek(id, status)
        if pending is None or self._control is None or not ruling.consume:
            return ruling, pending
        if await self._control.consume(id, pending.verb):
            return ruling, pending
        logger.info("control intent for run %s changed under this caller; re-reading it", id)
        return await self._peek(id, status)

    async def _claim_session(self, opening: RunStarted, spec: InvocableSpec, ctx: RunContext) -> Event:
        """Open this run, or refuse the turn: the store decides, in one conditional append.

        A session's engine state is one conversation, and only its engine can lock it — so the
        platform admits one turn at a time and the write that opens a run is the write that
        tests whether the session is free. A check followed by an append would let two servers
        both find it idle; here only one ``run.started`` can land, so the loser is told, not
        interleaved with the winner. Refusing raises rather than yielding nothing, because a
        caller cannot tell an empty stream apart from a turn that produced no events.

        An open run nobody is coming back for would otherwise hold its session for good: every
        graceful exit closes its run, so this is the process that was killed outright. Such a
        run stops holding the session once it has been silent for ``stale_run_after``, and this
        turn closes it — loudly, and accepting that a takeover can be premature, because a
        session wedged forever is the worse failure. Failing to close it is not worth failing
        this turn over: the next one meets the same stale run and tries again.
        """
        claim, event = await self._store.claim_start(ctx.log_key, opening, ctx, spec.name, self._stale_run_after)
        if claim.held_by is not None or event is None:
            raise SessionBusyError(await self._session_busy_message(ctx, claim.held_by))
        for tail in claim.overridden:
            try:
                await self._close_abandoned(tail, ctx)
            except StoreError:
                # This run is already open in the log, so letting a failed piece of bookkeeping
                # out here would leave it with no terminal event and wedge the session for a
                # whole window. The abandoned run stays open instead, and the next turn — which
                # finds it just as stale — closes it then.
                logger.exception("could not close abandoned run %s; leaving it for the next turn", tail.run_id)
        await self._fan_out(event)
        return event

    async def _session_busy_message(self, ctx: RunContext, held_by: str | None) -> str:
        """What ``SessionBusyError`` says, which depends on why the holder is still open.

        A ``RUNNING`` holder really is "in flight." One parked at ``PAUSED`` or
        ``WAITING_ANSWER`` is not — nothing is executing it, and no ``stale_run_after`` will
        ever free it — so the message names the verb that actually unsticks that run instead
        of repeating a claim that is false of it.
        """
        status = None if held_by is None else await self._store.run_status(ctx.log_key, held_by, ctx)
        if status is RunStatus.WAITING_ANSWER:
            return (
                f"session {ctx.log_key!r} is held by run {held_by!r}, parked waiting for an answer — "
                f"supply it with deck.runs.answer(...) or end it with deck.runs.cancel(...), see {_SESSIONS_DOCS}"
            )
        if status is RunStatus.PAUSED:
            return (
                f"session {ctx.log_key!r} is held by run {held_by!r}, paused — "
                f"lift it with deck.runs.resume(...) or end it with deck.runs.cancel(...), see {_SESSIONS_DOCS}"
            )
        return (
            f"session {ctx.log_key!r} already has run {held_by!r} in flight, "
            f"so run {ctx.run_id!r} cannot start on it — see {_SESSIONS_DOCS}"
        )

    async def _close_abandoned(self, tail: Event, ctx: RunContext) -> None:
        """Close a run this turn took the session from. Nobody else can: its process is gone,
        and an open run in the log is indistinguishable from one still in flight.

        Written in *that* run's context, not this turn's, so the store stamps it with the
        abandoned run's own ``run_id``, ``session_id`` and next ``seq``, and it inherits that
        run's ``origin``. The event belongs to its story: a reader must not find this invocable
        blamed for it. ``tail`` is the run's last event, handed over by the claim that stepped
        over it — the store had already read it to decide the run was stale, so nothing here
        goes back for it.
        """
        logger.warning(
            "run %s went silent holding session %s; run %s took it over and closed it as failed",
            tail.run_id,
            ctx.log_key,
            ctx.run_id,
        )
        payload = RunFailed(
            error_code="cancelled_hard",
            message=f"abandoned: the session was taken over by run {ctx.run_id}",
            retryable=False,
        )
        abandoned = replace(ctx, run_id=tail.run_id, session_id=tail.session_id)
        event = (await self._store.append(ctx.log_key, [payload], abandoned, tail.origin))[0]
        await self._fan_out(event)

    async def _close_cancelled(self, spec: InvocableSpec, ctx: RunContext, reason: str) -> None:
        """Write the closing ``run.cancelled`` while this task is already being cancelled.

        Shielded because the append suspends — a durable store hands it to a thread, and the
        in-memory one yields a turn — and an unshielded await inside a cancelled task is
        re-cancelled before the write can land. Best effort by construction: the write survives
        the cancellation, but not the event loop, so a process dying with the request leaves the
        run open in the log for whatever reconciles it later.
        """
        recording = asyncio.ensure_future(self._record(RunCancelled(reason=reason), spec, ctx))
        with suppress(asyncio.CancelledError):
            await asyncio.shield(recording)

    async def _claim_resume(
        self, spec: InvocableSpec, ctx: RunContext, value: Any, reason: str | None = None
    ) -> Event | None:
        """Take the run's suspended -> ``RUNNING`` transition, or ``None`` if someone else
        already has it. Suspended is ``WAITING_ANSWER`` or ``PAUSED``: the same claim serves
        both, because both are one run owed a terminal event and only one caller may continue
        it (``can_resume``).

        The store decides, in one conditional append: whoever's ``run.resumed`` lands is the
        one caller that gets to play the run on. That holds across processes, where a check
        followed by a separate append never could — two servers sharing a store would both
        read the suspended status and both write. A loser reads nothing from the engine and
        yields nothing, so a stray resume stays a no-op rather than an error.

        The claim carries the answer, not just the fact of it: this one append is what flips
        the status, so a value written anywhere else would leave a window in which the log
        says the run was answered and no longer holds what the answer was — and the engine,
        still parked at its interrupt, could never be brought back in line with it.

        ``seq`` continues across a process restart rather than resetting, because the store
        assigns it from the run's own log (ADR-D11) — there is no counter here to recover.
        """
        resumed = RunResumed(reason=reason, value=_as_content(value, ctx.run_id))
        event = await self._store.claim_resume(ctx.log_key, ctx.run_id, resumed, ctx, spec.name)
        if event is None:
            return None
        await self._fan_out(event)
        return event

    async def pending(self, *, namespace: str | None = None) -> list[PendingRun]:
        """Every run currently ``WAITING_ANSWER`` in this namespace.

        Asks the store to project which runs are waiting rather than keeping an in-memory
        registry — a registry would go stale the moment a process restarted, which is
        exactly the bug this avoids. Only the matched runs get a (bounded, per-run) read,
        to pull the interrupt's ``thread_id`` and ``payload``.

        The listing and those reads are two snapshots, so a run can be resumed between them
        and come back already answered. That is harmless: the resume claim itself is what
        checks status, so acting on a stale entry is a no-op, not a double resume.
        """
        # ponytail: every parked run's whole log, per call, and an approval inbox polls this —
        # so the cost is (parked runs x their length) on a path a UI hits on a timer. Fine while
        # a deployment parks tens of runs; the upgrade is a store-side projection of each run's
        # last interrupt, and the trigger is the first inbox that pages or that a poll can't
        # answer inside its own refresh interval.
        ctx = self._context(namespace=namespace)
        out: list[PendingRun] = []
        for summary in await self._store.list_runs(ctx, status=RunStatus.WAITING_ANSWER):
            found = _last_interrupt(await self._store.read_run(summary.log_key, summary.run_id, ctx))
            if found is None:
                continue
            event, interrupted = found
            out.append(
                PendingRun(
                    run_id=summary.run_id,
                    session_id=event.session_id,
                    invocable=event.origin,
                    thread_id=interrupted.thread_id or summary.run_id,
                    payload=interrupted.payload,
                )
            )
        return out

    async def drain(self) -> None:
        """Flush what the sinks have not taken yet, then close them.

        The composition root calls this at shutdown: without it, queued emits are destroyed
        with the event loop and the last few audit or cost events are silently lost, and a sink
        that buffers internally never gets the one ``EventSinkPort.close`` that tells it to
        write its buffer out. Never called per event — that would be exactly the join the
        fan-out exists to avoid. It is terminal: closed sinks stay closed, and a run after this
        one reaches none of them.
        """
        await asyncio.gather(*(dispatch.close() for dispatch in self._sinks), return_exceptions=True)

    def _context(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        namespace: str | None = None,
        data: object = None,
    ) -> RunContext:
        """Build a context for addressing a run whose id is already known — resume, signal,
        answer, a lookup-only read. ``run_id`` here is that known id, carried through as-is;
        it is minted only by :meth:`_new_run_context`, never by this one.
        """
        return RunContext(run_id=run_id or str(uuid4()), session_id=session_id, namespace=namespace, data=data)

    def _new_run_context(
        self,
        *,
        key: str | None = None,
        session_id: str | None = None,
        namespace: str | None = None,
        data: object = None,
    ) -> RunContext:
        """Mint a fresh run's context — the one place a new run's ``run_id`` is minted.

        ``key`` is the caller's optional identifier, carried through unchanged as ``ctx.key``:
        it is never the source of ``run_id``, which is why this is a separate method from
        :meth:`_context` rather than that one falling back to ``uuid4()``. A caller-supplied
        value reaching ``run_id`` is exactly the derivation this design retired — two namespaces
        given the same ``key`` must still mint two different, unrelated ids.
        """
        return RunContext(run_id=str(uuid4()), key=key, session_id=session_id, namespace=namespace, data=data)

    def _bind(self, ctx: RunContext) -> tuple[RunContext, deque[KnownPayload]]:
        """Give this run its control gate and its report buffer, and hand back both.

        The Runtime, not the caller, decides whether a run is cancellable and where its status
        and progress reports go — a caller builds a plain ``RunContext`` and never has to know
        that a ``ControlPort`` or a buffer exists. The buffer is per run and returned rather than
        stored, so two concurrent runs on one Runtime can never drain into each other.
        """
        reports: deque[KnownPayload] = deque()
        gate = (
            ctx.gate
            if self._control is None
            else Gate(self._control, ctx.id, poll_interval=self._control_poll_interval)
        )
        return replace(ctx, gate=gate, reporter=Reporter(reports)), reports

    async def _drain(
        self, reports: deque[KnownPayload], spec: InvocableSpec, ctx: RunContext
    ) -> AsyncGenerator[Event, None]:
        """Record whatever the run reported about itself since the last event.

        Called just *before* each engine payload, never after: that payload may be terminal, and
        nothing may follow a terminal event into the log. So a report is always in order and
        always inside the run, and one emitted after the engine's final payload is dropped — the
        ceiling ``core/reporting.py`` states.

        The count is taken once. A report arriving while these are being written belongs to the
        next payload's batch, so an emitter in a loop cannot starve the engine's own event.

        A store that refuses a report costs the report, never the run: an advisory event is not
        worth a run, and the alternative is a store that dislikes one *kind* turning a run that
        would have completed into ``run.failed``. It costs the report only — a refused append
        never took a number, so the log this leaves behind is dense.
        """
        for _ in range(len(reports)):
            payload = reports.popleft()
            try:
                yield await self._record(payload, spec, ctx)
            except StoreError:
                logger.warning("run %s could not record its %s; dropping the report", ctx.run_id, payload.kind)

    def _resolve(self, name: str) -> tuple[InvocableSpec, EnginePort]:
        spec = self._invocables.get(name)
        if spec is None:
            raise NotFoundError(f"no invocable named {name!r}")
        engine = self._engines.get(spec.engine)
        if engine is None:
            raise NotFoundError(f"{name!r} needs engine {spec.engine!r}, which is not registered")
        return spec, engine

    async def _record(self, payload: KnownPayload, spec: InvocableSpec, ctx: RunContext) -> Event:
        """Persist, fan out, return the event to yield — in that order.

        The store stamps it (ADR-D11): ``seq`` and ``ts`` are assigned in the same indivisible
        step that writes the row, so a refused append cannot leave a number spent. Nothing here
        holds a counter to get wrong.
        """
        event = (await self._store.append(ctx.log_key, [payload], ctx, spec.name))[0]
        await self._fan_out(event)
        return event

    async def _fan_out(self, event: Event) -> None:
        """Sinks get a copy of the stream and no say in it: never called inline, never fatal.

        Each sink gets a queue put rather than an ``emit``, and a full queue costs one loop
        turn before it starts dropping — so the run is never waiting on a sink, only ever on
        the loop it already shares with one.
        """
        for dispatch in self._sinks:
            await dispatch.submit(event)


def _as_content(value: Any, run_id: str) -> Input | None:
    """A resume answer as content blocks, so the log holds the input and not merely the fact
    that one arrived.

    Content stays content — the field's own type, so an approval typed at an inbox is the
    ``TextBlock`` it was sent as rather than a data block wrapping one. Everything else is
    JSON data, which is what a caller answering over HTTP or a graph resuming with a state
    object actually sends. A value JSON cannot carry is the one case that records nothing:
    losing the answer is better than failing a resume that would otherwise work, and the
    warning says which run to go and look at.
    """
    if value is None:
        return None
    # `[]` reaches coerce_input's list branch vacuously, and recording it as content with no
    # blocks would say an answer arrived and was blank. It is the empty JSON array: an answer.
    # A type check, not a comparison: `!=` runs the caller's own `__ne__`, and an array-like
    # answer (ndarray, Series) returns elementwise and then raises on `bool()`.
    if not (isinstance(value, list) and not value):
        try:
            return coerce_input(value)
        except TypeError:
            pass
    try:
        return [DataBlock(data=value)]
    except ValidationError:
        # Not "was resumed": this runs before the claim, so the caller may still lose it.
        logger.warning("the answer for run %s is a %s, which the log cannot hold", run_id, type(value).__name__)
        return None


def _failed(exc: Exception, engine: str) -> RunFailed:
    """The record for an exception the Runtime caught. The type name only — an exception message
    can carry content that must not reach a sink.

    A log that could not be written is not the engine misbehaving, so the record does not say it
    was. ``error_code`` stays ``engine_error`` either way: the closed set has no entry for a store
    fault, and minting one is a schema change rather than this line's business.
    """
    if isinstance(exc, StoreError):
        return _engine_failed(f"{type(exc).__name__} recording this run")
    return _engine_failed(f"{type(exc).__name__} in engine {engine!r}")


def _engine_failed(message: str) -> RunFailed:
    return RunFailed(error_code="engine_error", message=message, retryable=False)


def _last_interrupt(events: Sequence[Event]) -> tuple[Event, RunInterrupted] | None:
    for event in reversed(events):
        if isinstance(event.payload, RunInterrupted):
            return event, event.payload
    return None


__all__ = ["PendingRun", "Runtime"]
