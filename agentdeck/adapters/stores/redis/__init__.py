"""The event log in Redis (ADR-D5: the platform record, not engine state)."""

from agentdeck.adapters.stores.redis.store import RedisEventStore

__all__ = ["RedisEventStore"]
