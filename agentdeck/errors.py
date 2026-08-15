"""One exception hierarchy: the errors the harness raises are ``AgentdeckError``s.

Lets consumers write a single ``except AgentdeckError`` and lets ``serve.py``
map errors to HTTP status without knowing every concrete type.

Not everything is migrated, deliberately: pydantic ``field_validator`` bodies
keep raising ``ValueError`` (pydantic only folds those into
``ValidationError``), and missing-path / workspace faults keep their stdlib
types (``FileNotFoundError``, ``RuntimeError``).
"""

from __future__ import annotations


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
    """A turn was asked for on a session that already has a run in flight.

    One session runs one turn at a time: a second turn would hand the engine a conversation
    the first one is still changing. Not a store failure — the log answered, and the answer
    was no. The message names the run holding the session, which is what a caller retries
    behind or reports.
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


__all__ = [
    "AgentdeckError",
    "ConfigError",
    "ContextTypeError",
    "NotFoundError",
    "RunStateError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
]
