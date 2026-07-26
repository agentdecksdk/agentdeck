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


__all__ = ["AgentdeckError", "ConfigError", "NotFoundError", "SkillError"]
