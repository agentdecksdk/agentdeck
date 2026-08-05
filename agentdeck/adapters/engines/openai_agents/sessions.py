"""The openai-agents adapter's private execution state (ADR-D5 §3).

``SessionFactory`` is relocated here unchanged from ``runtime/sessions.py`` — kept, not
rewritten, per the ADR: it mints per-id :class:`agents.extensions.memory.RedisSession`
objects sharing one Redis client. ``ExecutionStore`` is the adapter's own seam on top of
it: Redis-backed when a factory is configured, one in-process
:class:`agents.SQLiteSession` per key otherwise — the same fallback ``agentdeck.app.App``
already uses for v1 chat, so both callers of the SDK agree on what "no Redis configured"
means. Nothing outside this adapter directory may import either class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import SQLiteSession
from agents.extensions.memory import RedisSession
from redis.asyncio import Redis

if TYPE_CHECKING:
    from agents.memory.session import Session

    from agentdeck.runtime.settings import SessionSettings


class SessionFactory:
    """Builds per-id :class:`RedisSession` objects sharing one Redis client."""

    def __init__(
        self,
        redis_client: Redis,
        *,
        key_prefix: str = "agents:session",
        ttl: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl = ttl

    @classmethod
    def from_settings(cls, settings: SessionSettings) -> SessionFactory | None:
        """Build from :class:`SessionSettings` or return ``None`` when disabled."""
        if not settings.redis_url:
            return None
        return cls(
            Redis.from_url(settings.redis_url),
            key_prefix=settings.redis_key_prefix,
            ttl=settings.redis_ttl,
        )

    def session_for(self, session_id: str) -> Session:
        return RedisSession(
            session_id,
            redis_client=self._redis,
            key_prefix=self._key_prefix,
            ttl=self._ttl,
        )

    async def aclose(self) -> None:
        await self._redis.aclose()


class ExecutionStore:
    """The engine's execution memory, keyed by ``RunContext.log_key`` (session, or the run
    itself when there is no session).

    Not the event log: this is engine-native state (ADR-D5) that only ``OpenAIAgentsEngine``
    reads. ``session_factory`` set means Redis-backed and shared across processes; unset
    falls back to one in-process ``SQLiteSession`` per key, so tests and the M0 skeleton
    need no network.
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory
        self._local: dict[str, Session] = {}

    def session_for(self, key: str) -> Session:
        if self._session_factory is not None:
            return self._session_factory.session_for(key)
        return self._local.setdefault(key, SQLiteSession(key))

    async def aclose(self) -> None:
        if self._session_factory is not None:
            await self._session_factory.aclose()


__all__ = ["ExecutionStore", "SessionFactory"]
