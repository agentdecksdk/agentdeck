"""The openai-agents adapter's private execution state (ADR-D5: the engine's working
memory is its own, never read by consumers).

``SessionFactory`` is relocated here unchanged from ``runtime/sessions.py``  -  kept, not
rewritten: it mints per-id :class:`agents.extensions.memory.RedisSession`
objects sharing one Redis client. ``ExecutionStore`` is the adapter's own seam on top of
it: Redis-backed when a factory is configured, one in-process
:class:`agents.SQLiteSession` per key otherwise  -  the same fallback ``agentdeck.deck.Deck``
uses for its chat methods, so both callers of the SDK agree on what "no Redis configured"
means. Nothing outside this adapter directory may import either class.

This module is on every agent run's import path regardless of ``AGENTDECK_SESSION``
(``__init__.py`` imports it unconditionally), so both the redis client and
``agents.extensions.memory``  -  whose ``RedisSession`` import is itself gated on redis being
installed  -  are resolved lazily in ``from_settings``, only once a ``redis://`` URL is
actually configured, the same way the event stores resolve theirs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents import SQLiteSession

if TYPE_CHECKING:
    from agents.memory.session import Session
    from redis.asyncio import Redis

    from agentdeck.core.context import RunContext
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
        if not settings.url:
            return None
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise ImportError(
                'a redis:// AGENTDECK_SESSION needs the redis client  -  install the "redis" extra: '
                'pip install "agentdeck-sdk[redis]"'
            ) from exc
        return cls(
            Redis.from_url(settings.url),
            key_prefix=settings.redis_key_prefix,
            ttl=settings.redis_ttl,
        )

    def session_for(self, session_id: str) -> Session:
        # Reached only once `from_settings` has already resolved a real Redis client, so
        # the redis extra is known installed by the time this import runs.
        from agents.extensions.memory import RedisSession

        return RedisSession(
            session_id,
            redis_client=self._redis,
            key_prefix=self._key_prefix,
            ttl=self._ttl,
        )

    async def aclose(self) -> None:
        await self._redis.aclose()


class ExecutionStore:
    """The engine's execution memory, keyed by ``(namespace, session or run)``  -  session,
    or the run itself when there is no session.

    Not the event log: this is engine-native state (ADR-D5) that only ``OpenAIAgentsExecutor``
    reads. ``session_factory`` set means Redis-backed and shared across processes; unset
    falls back to one in-process ``SQLiteSession`` per key, so tests and the M0 skeleton
    need no network. The namespace prefix matters even though that key is usually a
    server-generated session id: two namespaces are free to pick the same one, and without
    it their conversations would share one SDK session  -  exactly the isolation the event
    stores already enforce (``adapters/stores/memory``'s namespace-scoped buckets).
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory
        # Precisely SQLiteSession, not the broader Session protocol: aclose() below
        # needs its close(), which isn't part of SessionABC.
        self._local: dict[str, SQLiteSession] = {}

    def session_for(self, ctx: RunContext) -> Session:
        # The conversation this turn continues: the session when there is one, the run
        # itself when there is not. Derived here rather than carried on the context,
        # because it names *this engine's* memory and nothing else reads it.
        key = f"{ctx.namespace_key}:{ctx.session_id or ctx.run_id}"
        if self._session_factory is not None:
            return self._session_factory.session_for(key)
        return self._local.setdefault(key, SQLiteSession(key))

    async def aclose(self) -> None:
        # ponytail: SQLiteSession.close() is sync and cheap for the :memory:/local-file
        # fallback this is  -  a pool with async teardown is follow-up work if a real
        # deployment ever wants the local path instead of Redis.
        for session in self._local.values():
            session.close()
        if self._session_factory is not None:
            await self._session_factory.aclose()


__all__ = ["ExecutionStore", "SessionFactory"]
