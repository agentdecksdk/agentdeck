"""One exception hierarchy: every error the harness raises is an ``AgentdeckError``.

Lets consumers write a single ``except AgentdeckError`` and lets ``serve.py``
map errors to HTTP status without knowing every concrete type.
"""

from __future__ import annotations


class AgentdeckError(Exception):
    """Base for every error agentdeck raises."""


class NotFoundError(AgentdeckError, KeyError):
    """Unknown agent / workflow / skill name.

    Also a ``KeyError`` so existing ``except KeyError`` call sites (registry
    lookups predate this hierarchy) keep working unchanged.
    """

    def __str__(self) -> str:
        # KeyError.__str__ reprs a single arg (quotes the message); override
        # back to the plain message for logs and the serve.py 404 body.
        return self.args[0] if len(self.args) == 1 else super().__str__()


class SkillError(AgentdeckError):
    """Base for skill-execution failures (``SkillExecutionError``, ``SkillEnvError``)."""


class ConfigError(AgentdeckError):
    """Invalid or incomplete configuration."""


__all__ = ["AgentdeckError", "ConfigError", "NotFoundError", "SkillError"]
