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


class StoreError(AgentdeckError):
    """A durable store failed — the event log, or the control-signal rows beside it.

    The one type a store adapter raises outward: whatever library it is built on keeps its
    own exceptions behind the port, chained onto this one as ``__cause__``. Losing a race
    is not one of these — a refused claim is a ``False``, an unreachable store is an error.
    """


__all__ = ["AgentdeckError", "ConfigError", "NotFoundError", "SkillError", "StoreError"]
