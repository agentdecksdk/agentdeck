"""What a run carries with it: who asked, which run, and the limits it was admitted under.

Passed explicitly to every port instead of read from ambient state — that is what makes
isolation and tracing testable, and it is why an engine can never invent a namespace.
Frozen: a run's identity cannot change mid-flight.

Deliberately holds no application identity. AgentDeck runs agents; it does not model users,
organizations or permissions, so nothing here says who is acting or what they may do. An
application that has those concepts keeps them, and may project one of them onto
``namespace`` — which AgentDeck then treats as an opaque key it never interprets. ``data`` is
not a counter-example: it is application-*owned*, an environment the application hands the run,
and AgentDeck reads it only to hand it back. Owning a value is not being identified by it.

:class:`Context` is the public half of the same subject — the restricted view application code
receives, so a tool signature names one AgentDeck type instead of an engine's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from agentdeck.core.control import Gate
from agentdeck.core.reporting import Reporter


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's identity and limits.

    ``namespace`` is an opaque isolation boundary and nothing more. AgentDeck never parses it,
    never compares its parts, and attaches no meaning to it — an application may key it by
    workspace, project, business or anything else, and ``None`` is a first-class mode, not a
    placeholder. It says which runs are kept apart, never who is acting or what they may do.

    Empty is rejected rather than accepted, because stores encode ``None`` as the empty key —
    so an explicit ``""`` would silently share a bucket with unnamespaced runs.

    ``run_id`` is minted once per run and never a fifth value alongside it: :attr:`id` is a
    plain read of this same field, not a computation over several. It used to be derived from
    ``namespace`` too (``encode()``, removed in #324) so that two namespaces reusing one
    caller-chosen value would still address different control-plane rows; now that ``run_id``
    is minted rather than caller-chosen, two namespaces never produce the same value in the
    first place, and there is nothing left to derive.

    ``key`` is the caller's optional stable application identifier — for lookup and
    idempotency, never for addressing. It plays no part in :attr:`id` or :attr:`log_key`, and
    a store indexes ``(namespace, key)`` as a separate, permanent claim.

    Four values and three seams, and nothing else: a field AgentDeck's own machinery never
    reads is not infrastructure, it is a guess about a mechanism that does not exist yet.
    ``trace_id``, ``budget``, ``triggered_by``, ``parent_run_id``, ``deadline`` and
    ``idempotency_key`` were all of that, and each comes back with the thing that enforces it.
    ``data`` is the fourth value because it arrives with that thing: the engine bridges read it
    on every injected call to build the :class:`Context` a user callable declared.

    ``data`` is opaque by construction — ``object``, never inspected, never copied, never
    serialized into an event, and left out of the repr so a logged context cannot leak a DB
    client or a customer record. It is application-*owned*, which is not the application
    *identity* ``namespace`` carefully is not either: ``namespace`` says which runs are kept
    apart, ``data`` says what this one was handed to work with, and neither says who is acting.

    ``gate`` and ``reporter`` are two of the three fields that are not values — a cooperative seam
    has to reach code the Runtime never sees. Both default to doing nothing and only the Runtime
    rebinds them, so a context built by hand is still a plain value object.

    ``tool_failures`` is the third: the openai-agents engine's own seam, not the Runtime's. A
    compiled tool that raises is caught deep inside the Agents SDK, where the only way back out
    is the ``failure_error_function`` the SDK calls to format the model-visible message — so
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
    def log_key(self) -> str:
        """Where this run's events are written — a run without a session is its own log,
        so persist-before-yield holds for it too."""
        return self.session_id or self.run_id

    @property
    def id(self) -> str:
        """This run's durable address — what the control plane addresses it by, everywhere.

        A carried value, not a computed one: a plain read of :attr:`run_id`, which the store
        mints once per run and persists. There is no second value this could disagree with,
        because there is no second source — unlike the ``namespace``-derived address it
        replaces, minting alone is what keeps two namespaces from ever producing the same id.
        """
        return self.run_id


@dataclass(frozen=True, slots=True)
class Context[T]:
    """The only public context type: what a user callable declaring ``Context[T]`` receives.

    One portable type above two engines. The OpenAI SDK hands a tool its own
    ``RunContextWrapper`` and LangGraph hands a node its own ``Runtime``; each engine bridge
    unwraps its native carrier to the :class:`RunContext` travelling inside and presents this
    view, so a tool signature does not change when the engine does.

    A view, not a copy — ``data`` is the very object the caller supplied, by reference. Access
    to it is access for *application* code only: nothing here is ever serialized into a prompt,
    and a dynamic-instructions callable contributes only its return value to what the model sees.

    Narrower than the carrier on purpose. ``namespace`` is absent because no injection site has
    needed to read it, and ``gate`` is absent because :meth:`checkpoint` is the whole of what a
    callable may do with it — adding a property later is cheaper than changing one after release.
    """

    _run: RunContext

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

    async def checkpoint(self) -> None:
        """Offer a safe point: returns, or raises if the run was signaled to stop or pause.

        Deliberately takes no safe-point argument. The kinds of safe point are a recorded
        contract engine adapters share, and a user callable naming a new one would change what
        the event log means from outside the engines.
        """
        await self._run.gate.checkpoint()
