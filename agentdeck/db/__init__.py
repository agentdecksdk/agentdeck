"""Optional database connector for the SysAgentsDB knowledge service.

A single entry point — :func:`db_connector` — yields a lifecycle-managed
HTTP-mode SDK client when a database is configured and ``None`` when it is not,
so a workflow can run unchanged with or without a backing database.
:func:`db_available` reports configuration without opening a connection.

Configuration lives in :class:`~agentdeck.runtime.settings.DbSettings`
(env ``AGENTDECK_DB_*``).
"""

from __future__ import annotations

from agentdeck.db.connector import db_available, db_connector

__all__ = ["db_available", "db_connector"]
