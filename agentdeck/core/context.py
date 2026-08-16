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
from urllib.parse import quote

from agentdeck.core.control import Gate
from agentdeck.core.reporting import Reporter

DERIVED_PREFIX = "adr:"
"""Marks an id as namespace-derived (see :func:`encode`). Reserved: no caller-supplied
``run_id`` may start with it, or an unnamespaced id could collide with one."""


# ponytail: the id is derived here because nothing persists one yet — `runtime.signal` and the
# CLI both compute the address with no event store in reach, so a minted id would have no
# cross-process resolution. #324 mints and persists it, at which point `encode`, DERIVED_PREFIX
# and the `adr:` guard all go and `RunContext.id` becomes a carried value
# (docs/design/run-identity.md §11).
def encode(namespace: str | None, run_id: str) -> str:
    """A run's durable address: what a :class:`~agentdeck.core.ports.control.ControlPort`
    keys by, and the only thing that may ever reach one.

    Derived, not minted — same ``(namespace, run_id)`` always yields the same id, in any
    process, with no round trip and no mapping table to keep in sync.

    ``encode(None, run_id) == run_id`` is the compatibility keystone: every unnamespaced id is
    byte-identical to today's ``run_id``, so stored ids, the unnamespaced CLI and the frozen v1
    wire need no migration. A namespaced id is prefixed and percent-quoted (``safe=""``, so a
    ``:`` inside either part is escaped too) precisely so it can never collide with an
    unnamespaced one or with a different ``(namespace, run_id)`` pair.
    """
    if namespace is None:
        return run_id
    return f"{DERIVED_PREFIX}{quote(namespace, safe='')}:{quote(run_id, safe='')}"


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's identity and limits.

    ``namespace`` is an opaque isolation boundary and nothing more. AgentDeck never parses it,
    never compares its parts, and attaches no meaning to it — an application may key it by
    workspace, project, business or anything else, and ``None`` is a first-class mode, not a
    placeholder. It says which runs are kept apart, never who is acting or what they may do.

    Empty is rejected rather than accepted, because stores encode ``None`` as the empty key —
    so an explicit ``""`` would silently share a bucket with unnamespaced runs.

    Four values and three seams, and nothing else: a field AgentDeck's own machinery never
    reads is not infrastructure, it is a guess about a mechanism that does not exist yet.
    ``trace_id``, ``budget``, ``triggered_by``, ``parent_run_id``, ``deadline`` and
    ``idempotency_key`` were all of that, and each comes back with the thing that enforces it.
    ``data`` is the fourth value because it arrives with that thing: the engine bridges read it
    on every injected call to build the :class:`Context` a user callable declared.

    :attr:`id` is not a fifth value: it is computed from ``namespace`` and ``run_id`` on every
    read, never stored and never passed to the constructor, so no call site can build one that
    disagrees with the pair it was derived from.

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
        if self.run_id.startswith(DERIVED_PREFIX):
            raise ValueError(
                f"run_id must not start with {DERIVED_PREFIX!r}: that prefix is reserved for "
                "namespace-derived ids (see encode()), so a caller-supplied run_id starting "
                "with it could be crafted to collide with one and address a different run"
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

        Derived from :attr:`namespace` and :attr:`run_id` (:func:`encode`), never stored and
        never a constructor argument: there is no second value a call site could forget to
        thread, and no mapping table to keep in sync with the pair it is computed from.
        """
        return encode(self.namespace, self.run_id)


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
