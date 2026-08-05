"""The event log — the platform's record of what happened.

Not the engine's execution state: an engine that needs its exact prior items keeps them
privately (ADR-D5). This log is what replay, audit and every surface read from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentdeck.core.context import RunContext
    from agentdeck.core.events import Event


class SessionStorePort(ABC):
    """Append-only. A log holds every run of one session, so it is ordered by *append*, not
    by ``seq`` — ``seq`` restarts at 0 for each run and only orders events within it.

    ``log_key`` is the session the events belong to, or the run itself when there is no
    session (``RunContext.log_key``) — a store never has to decide where to put them.
    """

    @abstractmethod
    async def append(self, log_key: str, events: Sequence[Event], ctx: RunContext) -> None:
        """Write events in the order given. Must return only once they are durable, because
        the Runtime yields to consumers immediately after."""

    @abstractmethod
    async def read(self, log_key: str, ctx: RunContext) -> list[Event]:
        """Every event in the log, oldest first — one session's whole history."""

    @abstractmethod
    async def read_run(self, log_key: str, run_id: str, ctx: RunContext, from_seq: int = 0) -> list[Event]:
        """One run's events from ``from_seq`` onward, inclusive.

        A range read has to name the run: ``seq`` is per run, so a seq range over a whole
        log would splice together the tails of every run in it. This is what a consumer
        calls to refetch after spotting a gap.
        """

    @abstractmethod
    async def list_log_keys(self, ctx: RunContext) -> list[str]:
        """Every log key with at least one event for this tenant.

        What a pending-interrupts listing scans instead of keeping its own in-memory
        registry of runs — that registry would be exactly the kind of status-from-memory
        bug a process restart is supposed to expose.
        """


__all__ = ["SessionStorePort"]
