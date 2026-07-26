"""Redis-backed agent conversation memory shared across plugins.

Mints :class:`agents.extensions.memory.RedisSession` objects keyed by an
arbitrary session id (typically a thread / conversation id). The factory
owns one shared async :class:`Redis` client and is consumer-agnostic —
cold-start hydration from an external store is the caller's job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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


__all__ = ["SessionFactory"]
