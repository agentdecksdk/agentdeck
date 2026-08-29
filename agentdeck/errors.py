"""The exception hierarchy, defined in :mod:`agentdeck.core.errors` and re-exported here.

Core raises these too (``RunContext`` refuses to be modelled with a :class:`ConfigError`), and
core may import nothing outside itself, so the taxonomy lives in the innermost ring  -  which is
where ``CLAUDE.md`` §2 already put it. This module is the import path every consumer writes, and
the names below are the same class objects, so ``except ConfigError`` catches what it always did.
"""

from __future__ import annotations

from agentdeck.core.errors import (
    DOCS_URL,
    AgentdeckError,
    ConfigError,
    ContextTypeError,
    DuplicateKeyError,
    InputError,
    NotFoundError,
    RunStateError,
    RunSuspendedError,
    SessionBusyError,
    SkillError,
    StoreError,
    UnsupportedControlError,
)

__all__ = [
    "DOCS_URL",
    "AgentdeckError",
    "ConfigError",
    "ContextTypeError",
    "DuplicateKeyError",
    "InputError",
    "NotFoundError",
    "RunStateError",
    "RunSuspendedError",
    "SessionBusyError",
    "SkillError",
    "StoreError",
    "UnsupportedControlError",
]
