"""One exception hierarchy: the errors the harness raises are ``AgentdeckError``s.

Lets consumers write a single ``except AgentdeckError`` and lets ``serve.py``
map errors to HTTP status without knowing every concrete type.

Not everything is migrated, deliberately: pydantic ``field_validator`` bodies
keep raising ``ValueError`` (pydantic only folds those into
``ValidationError``), and missing-path / workspace faults keep their stdlib
types (``FileNotFoundError``, ``RuntimeError``).
"""

from __future__ import annotations

from typing import Any

from agentdeck.core.status import RunStatus

# The canonical docs-site origin (see tests/test_docs_site.py's SITE_LINK, which knows both
# live origins and treats this one as canonical). One place to fix on a domain change, so an
# error message can link a page here instead of repeating a URL at each site.
DOCS_URL = "https://agentdecksdk.com"


class AgentdeckError(Exception):
    """Base for every error agentdeck raises."""


class NotFoundError(AgentdeckError):
    """Unknown agent / workflow / skill name."""


class SkillError(AgentdeckError):
    """Base for skill-execution failures (``SkillExecutionError``, ``SkillEnvError``)."""


class ConfigError(AgentdeckError):
    """Invalid or incomplete configuration."""


class ContextTypeError(ConfigError):
    """A callable requires a ``Context[T]`` this deck's declared context type cannot satisfy.

    A configuration error rather than a kind of its own: it is raised at ``Deck.build()``,
    alongside every other "this catalog does not hold together" refusal, and a caller already
    catching :class:`ConfigError` around ``build()`` keeps catching it.
    """


class SessionBusyError(AgentdeckError):
    """A turn was asked for on a session another run already holds.

    One session runs one turn at a time: a second turn would hand the engine a conversation
    the first one is still changing. Not a store failure — the log answered, and the answer
    was no. The message names the run holding the session, and — when that run is parked
    waiting for an answer or a resume rather than actually running — the call that frees it,
    which is what a caller retries behind or reports.
    """


class RunStateError(AgentdeckError):
    """The operation is not one this run's state admits — answering a paused run, resuming one
    that is waiting for a value, or answering one an operator asked to stop.

    The state machine answered, and the answer was no; nothing was written. A refusal that came
    from the state alone names the operation that *would* have worked, because a caller holding a
    ``run_id`` off a stream it was watching has no other way to find out which one that is. A
    refusal that came from a pending signal names none, deliberately: with a pause outstanding
    every verb is refused, so there is no path to point at (``docs/design/run-lifecycle.md``).
    """


class StoreError(AgentdeckError):
    """A durable store failed — the event log, or the control-signal rows beside it.

    The one type a store adapter raises outward: whatever library it is built on keeps its
    own exceptions behind the port, chained onto this one as ``__cause__``. Losing a race
    is not one of these — a refused claim is a ``False``, an unreachable store is an error.
    """


class DuplicateKeyError(AgentdeckError):
    """A run started with a ``key`` already claimed by another run in the same namespace.

    ``(namespace, key)`` is consumed permanently once a run opens with it — not merely while
    that run is active — so this is not a race retried away; it is the store refusing a second
    start rather than silently handing back the run that already holds the key. The caller's
    recovery path is ``get(namespace=, key=)``, not a retry.
    """


class RunSuspendedError(RunStateError):
    """``await run`` reached a run that stopped suspended — ``PAUSED`` or ``WAITING_ANSWER`` —
    rather than completing, failing or being cancelled.

    There is no timeout parameter to wait either state out (``docs/design/run-identity.md``
    §15): a caller who wanted to block would hang forever if nobody ever resumes or answers it,
    so this raises instead. ``pending`` carries what :meth:`Run.answer` would need — the
    interrupt's own payload — and is ``None`` for a plain pause, which only ``run.resume()``
    lifts.
    """

    def __init__(self, run_id: str, status: RunStatus, pending: Any = None) -> None:
        verb = "run.answer(...)" if status is RunStatus.WAITING_ANSWER else "run.resume()"
        super().__init__(f"run {run_id!r} is {status.value}, not done: call {verb} instead of awaiting it.")
        self.status = status
        self.pending = pending


__all__ = [
    "AgentdeckError",
    "ConfigError",
    "ContextTypeError",
    "DuplicateKeyError",
    "NotFoundError",
    "RunStateError",
    "RunSuspendedError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
]
