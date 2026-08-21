"""What a run carries with it: who asked, which run, and the limits it was admitted under.

Passed explicitly to every port instead of read from ambient state  -  that is what makes
isolation and tracing testable, and it is why an engine can never invent a namespace.
Frozen: a run's identity cannot change mid-flight.

Deliberately holds no application identity. AgentDeck runs agents; it does not model users,
organizations or permissions, so nothing here says who is acting or what they may do. An
application that has those concepts keeps them, and may project one of them onto
``namespace``  -  which AgentDeck then treats as an opaque key it never interprets. ``data`` is
not a counter-example: it is application-*owned*, an environment the application hands the run,
and AgentDeck reads it only to hand it back. Owning a value is not being identified by it.

:class:`ToolCtx` is the public half of the same subject  -  the restricted view application code
receives, so a tool signature names one AgentDeck type instead of an engine's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from agentdeck.core.control import Gate, RunPausedError
from agentdeck.core.events import KnownPayload, RunInterrupted
from agentdeck.core.reporting import Reporter

if TYPE_CHECKING:
    from agentdeck.core.base import JsonData


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's identity and limits.

    ``namespace`` is an opaque isolation boundary and nothing more. AgentDeck never parses it,
    never compares its parts, and attaches no meaning to it  -  an application may key it by
    workspace, project, business or anything else, and ``None`` is a first-class mode, not a
    placeholder. It says which runs are kept apart, never who is acting or what they may do.

    Empty is rejected rather than accepted, because stores encode ``None`` as the empty key  -
    so an explicit ``""`` would silently share a bucket with unnamespaced runs.

    ``run_id`` is minted once per run and never a fifth value alongside it: :attr:`id` is a
    plain read of this same field, not a computation over several. It used to be derived from
    ``namespace`` too (``encode()``, removed in #324) so that two namespaces reusing one
    caller-chosen value would still address different control-plane rows; now that ``run_id``
    is minted rather than caller-chosen, two namespaces never produce the same value in the
    first place, and there is nothing left to derive.

    ``key`` is the caller's optional stable application identifier  -  for lookup and
    idempotency, never for addressing. It plays no part in :attr:`id`, and
    a store indexes ``(namespace, key)`` as a separate, permanent claim.

    Four values and three seams, and nothing else. The four are the whole of a run's identity:
    :attr:`run_id` is which run, :attr:`key` is what the application calls it, :attr:`namespace`
    is what it is kept apart from, and :attr:`session_id` is the conversation it belongs to. There
    was a fifth, derived one (``log_key``, ``session_id or run_id``): it answered "which stream do
    these events go in", and a store handed it could no longer tell a session named after a run
    from that run itself. Beyond identity, a field AgentDeck's own machinery never reads is not
    infrastructure, it is a guess about a mechanism that does not exist yet. ``trace_id``,
    ``budget``, ``triggered_by``, ``parent_run_id``, ``deadline`` and ``idempotency_key`` were all
    of that, and each comes back with the thing that enforces it.
    ``data`` is the fourth value because it arrives with that thing: the engine bridges read it
    on every injected call to build the :class:`ToolCtx` a user callable declared.

    ``data`` is opaque by construction  -  ``object``, never inspected, never copied, never
    serialized into an event, and left out of the repr so a logged context cannot leak a DB
    client or a customer record. It is application-*owned*, which is not the application
    *identity* ``namespace`` carefully is not either: ``namespace`` says which runs are kept
    apart, ``data`` says what this one was handed to work with, and neither says who is acting.

    ``gate`` and ``reporter`` are two of the three fields that are not values  -  a cooperative seam
    has to reach code the Runtime never sees. Both default to doing nothing and only the Runtime
    rebinds them, so a context built by hand is still a plain value object.

    ``tool_failures`` is the third: the openai-agents engine's own seam, not the Runtime's. A
    compiled tool that raises is caught deep inside the Agents SDK, where the only way back out
    is the ``failure_error_function`` the SDK calls to format the model-visible message  -  so
    ``compile_tool`` records the exception here, keyed by the SDK's own ``call_id``, and the
    engine's translator reads it back onto ``tool.call.completed.error`` once the matching result
    arrives. Left out of the repr for the same reason as ``data``: an exception message can carry
    whatever the failing tool's arguments carried.
    """

    run_id: str
    session_id: str | None = None
    namespace: str | None = None
    key: str | None = None
    data: object = field(default=None, repr=False)
    gate: Gate = field(default_factory=Gate)
    reporter: Reporter = field(default_factory=Reporter)
    tool_failures: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.namespace is not None and not self.namespace:
            raise ValueError(
                "namespace must be a non-empty string or None; empty is how stores encode "
                "'no namespace', so an explicit '' would share a bucket with unnamespaced runs"
            )

    @property
    def namespace_key(self) -> str:
        """The namespace as a store keys by it: ``None`` is the empty key.

        One encoding, defined once, because four stores that each decided for themselves what
        "no namespace" looks like would be four chances to put one run in two buckets.
        """
        return self.namespace or ""

    @property
    def id(self) -> str:
        """This run's durable address  -  what the control plane addresses it by, everywhere.

        A carried value, not a computed one: a plain read of :attr:`run_id`, which the store
        mints once per run and persists. There is no second value this could disagree with,
        because there is no second source  -  unlike the ``namespace``-derived address it
        replaces, minting alone is what keeps two namespaces from ever producing the same id.
        """
        return self.run_id


@dataclass(frozen=True, slots=True)
class ToolCtx[T]:
    """The only public context type: what a user callable declaring ``ToolCtx[T]`` receives.

    One portable type above every executor. The OpenAI SDK hands a tool its own
    ``RunContextWrapper``; each bridge unwraps its native carrier to the :class:`RunContext`
    travelling inside and presents this view, so a tool signature does not change when the
    engine does.

    A view, not a copy  -  ``data`` is the very object the caller supplied, by reference. Access
    to it is access for *application* code only: nothing here is ever serialized into a prompt,
    and a dynamic-instructions callable contributes only its return value to what the model sees.

    Narrower than the carrier on purpose. ``namespace`` is absent because no injection site has
    needed to read it, and ``gate`` is absent because :meth:`safepoint` is the whole of what a
    callable may do with it  -  adding a property later is cheaper than changing one after release.

    ``_channel`` is present when the executor playing this body can stop it in place, and absent
    when it cannot: a tool inside an Agents SDK turn has no way to park, because the SDK owns the
    stack and the turn is replayed from the log on resume.

    A tool is a leaf capability, so this is where the surface stops: orchestration
    (``invoke``, ``parallel``, ``ask``, ``approve``) is :class:`WorkflowCtx`'s, and a tool that
    declared it would silently acquire the ability to coordinate other executions
    (``docs/design/execution-api.md``).
    """

    _run: RunContext
    _channel: Suspender | None = None

    @property
    def data(self) -> T:
        """The value the caller passed to ``run(context=...)``.

        The carrier stores it as ``object`` because AgentDeck never interprets it; ``T`` is the
        declaring callable's claim about it, checked where the context enters the run rather
        than re-checked on every read.
        """
        return cast("T", self._run.data)

    @property
    def reporter(self) -> Reporter:
        return self._run.reporter

    @property
    def run_id(self) -> str:
        return self._run.run_id

    @property
    def session_id(self) -> str | None:
        return self._run.session_id

    async def safepoint(self) -> None:
        """Offer a safe point: returns, or stops the run here if one was signaled.

        How it stops depends on what is playing this body, and that is the whole difference:
        where the body can be parked it waits in place with its locals intact, and where it
        cannot it unwinds and the run is replayed from the log on resume. A cancel always
        unwinds, because the run is over and there is nothing left to preserve.

        Deliberately takes no safe-point argument. The kinds of safe point are a recorded
        contract executor adapters share, and a user callable naming a new one would change what
        the event log means from outside the executors.
        """
        try:
            await self._run.gate.checkpoint()
        except RunPausedError as paused:
            if self._channel is None:
                raise
            # The request and the observation are records; the ``run.paused`` they end in is what
            # the run is actually suspended by, so it is the only one that waits.
            *recorded, suspending = paused.payloads
            for payload in recorded:
                await self._channel.emit(payload)
            await self._channel.suspend(suspending)


class Suspender(Protocol):
    """How a native body reaches the run it is inside: record something, or stop and wait.

    Bound by the native executor, which is the only thing that can honour the waiting half  -  the
    body is a live coroutine, so a suspension is a real wait rather than an unwind
    (``docs/design/execution-api.md``).
    """

    async def emit(self, payload: KnownPayload) -> None:
        """Record a payload on the run and keep going."""
        ...

    async def suspend(self, payload: KnownPayload) -> Any:
        """Record a suspending payload, park here, and return whatever answers it."""
        ...


@dataclass(frozen=True, slots=True)
class WorkflowCtx[T](ToolCtx[T]):
    """What an imperative ``@workflow`` body receives: :class:`ToolCtx` plus orchestration.

    The split is the semantic rule, not a convenience: a tool performs a capability, a workflow
    coordinates executions, and a tool that could ``ask`` a person or start another run is no
    longer a leaf. Declaring the wrong one is a ``build()`` error.

    ``invoke`` and ``parallel`` join this in the PR that adds child runs; what is here is the
    half that suspends the current branch. Both need somewhere to park, so a workflow is only
    ever built by an executor that can provide one.

    There is no ``approve()``: an approval is a question with two options, and one mechanism that
    takes any option set beats two that overlap. It also keeps AgentDeck out of the business of
    deciding what counts as a yes  -  an answer equals one of the options the asker wrote, or it
    is refused.
    """

    async def ask(self, question: str, *, options: list[JsonData] | None = None, **fields: JsonData) -> Any:
        """Suspend this branch until somebody answers ``question``, and return their answer.

        The run becomes ``WAITING_ANSWER`` and shows up in ``deck.runs.list(status=...)`` and the
        approval inbox; ``run.answer(value)`` is what continues it. The body is not unwound  -  it
        waits where it stands, locals intact  -  which is why an answer resumes the next line
        rather than replaying the workflow.

            approved = await ctx.ask("deploy to prod?", options=[True, False])
            env = await ctx.ask("which environment?", options=["dev", "prod"])

        ``options`` is what turns a question into a choice. They travel on the interrupt, so
        every surface that lists pending runs can render them, and an answer that is not one of
        them is refused before it is recorded  -  the answerer is told, and the run stays waiting.
        Without them any value is an answer, and the body is the only thing that can judge it.
        """
        asked = _asked(question, fields if options is None else {"options": options, **fields})
        return await self._waiting.suspend(asked)

    @property
    def _waiting(self) -> Suspender:
        """The channel this body parks on. Absent only on a context built by hand: an executor
        that plays a workflow is one that can suspend it."""
        if self._channel is None:
            raise RuntimeError(
                "this WorkflowCtx has no way to suspend its run, so ask()/approve() cannot wait "
                "for an answer. A workflow context is built by the executor playing it; one "
                "constructed by hand has no run to park."
            )
        return self._channel


def _asked(question: str, fields: dict[str, JsonData]) -> RunInterrupted:
    if not question:
        raise ValueError("ask() needs a question; an empty one has no answer to wait for.")
    return RunInterrupted(interrupt_id=uuid4().hex, reason="human", payload={"question": question, **fields})
