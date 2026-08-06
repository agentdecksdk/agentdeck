"""The event log in Postgres (ADR-D5: the platform record, not engine state)."""

from agentdeck.adapters.stores.postgres.store import PostgresEventStore

__all__ = ["PostgresEventStore"]
