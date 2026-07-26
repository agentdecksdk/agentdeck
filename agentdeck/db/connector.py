"""Optional connector to the SysAgentsDB knowledge service (HTTP-mode SDK).

One context manager — :func:`db_connector` — that yields a started
:class:`agentdecks_knowledge.sdk.Client` when a database is configured, or
``None`` when it is not. That single shape lets one workflow run the *same way*
with or without a database::

    async with db_connector() as db:
        if db is not None:
            await db.requirements.save(...)

— no ``try``/``except``, no parallel "with-DB" / "without-DB" pipelines.
:func:`db_available` reports configuration without opening a connection, for
callers that want to branch before doing work.

The harness talks to a *running* ``agentdeck-api`` over HTTP — never the
in-process self-host engine. Configuration is
:class:`~agentdeck.runtime.settings.DbSettings` (env ``AGENTDECK_DB_*``); when
``base_url`` is empty the connector is disabled and yields ``None``. The SDK
import is lazy so importing this module (and any workflow that uses it) never
requires the ``[sdk]`` extra unless a run actually reaches the service.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from agentdeck.runtime.settings import DbSettings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentdecks_knowledge.sdk import Client


def db_available(settings: DbSettings | None = None) -> bool:
    """Whether a knowledge-service base URL is configured (opens no connection).

    ``settings`` defaults to the process-wide ``get_settings().db``.
    """
    cfg = settings or get_settings().db
    return cfg.enabled


@asynccontextmanager
async def db_connector(settings: DbSettings | None = None) -> AsyncGenerator[Client | None]:
    """Yield a started SDK :class:`Client` (HTTP mode), or ``None`` when disabled.

    ``settings`` defaults to the process-wide ``get_settings().db``. When
    ``base_url`` is empty the connector yields ``None`` (a clean "no database
    configured" signal) instead of raising, so callers branch on ``if db:``.
    The transport is closed on exit.
    """
    cfg = settings or get_settings().db
    if not cfg.enabled:
        yield None
        return
    from agentdecks_knowledge.sdk import Client  # lazy: keeps [sdk] optional at import

    async with Client(**cfg.client_kwargs()) as client:
        yield client


__all__ = ["db_available", "db_connector"]
