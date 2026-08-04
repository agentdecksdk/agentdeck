"""The event log in a dict: the default for dev, tests and the contract suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.core.ports import SessionStorePort

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event


class MemoryEventStore(SessionStorePort):
    """Append-only lists, one per (tenant, log key). Process exit is data loss, by design.

    Keyed by tenant as well as log key so two tenants that pick the same session id cannot
    read each other's runs — isolation is not something a store gets to skip.
    """

    def __init__(self) -> None:
        self._logs: dict[tuple[str, str], list[Event]] = {}

    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        self._logs.setdefault((ctx.tenant, log_key), []).extend(events)

    async def read(self, log_key: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        log = self._logs.get((ctx.tenant, log_key), ())
        return [event for event in log if event.seq >= from_seq]


__all__ = ["MemoryEventStore"]
