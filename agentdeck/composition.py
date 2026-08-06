"""The composition root: the one place adapters are built and handed to a ``Runtime``.

Everything above this module takes ports; everything below it is an adapter — or, until v1's
runner glue is deleted, the `compat/` engine that stands in for one. ``App`` calls
:func:`build_runtime` and so does every other entry point that needs a real Runtime — the
demo script, the compat surface's tests — so a Runtime is assembled the same way
everywhere instead of hand-wired per caller. A second front door (a code-first ``Deck()``)
becomes another caller of this function rather than a second assembly.

Only the parts a caller actually varies are arguments; the rest resolve from settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import EventsSettings, get_settings
from agentdeck.v1bridge import V1CompatEngine, V1CompatWorkflowEngine

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from agents.memory.session import Session

    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import ControlPort, EnginePort, EventSinkPort, EventStorePort
    from agentdeck.workflows.base import BaseWorkflow


def build_runtime(
    *,
    engines: Sequence[EnginePort],
    invocables: Mapping[str, InvocableSpec] | None = None,
    store: EventStorePort | None = None,
    sinks: Sequence[EventSinkPort] = (),
    control: ControlPort | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Runtime:
    """Wire ``engines`` into a Runtime over the project's invocables.

    ``invocables`` defaults to discovery over ``./.agentdeck`` — pass a mapping to run
    specs built in code instead. ``store`` defaults to the configured event store, and
    ``clock`` to wall time.
    """
    engines = tuple(engines)
    specs = InvocableRegistry(engines).load() if invocables is None else invocables
    store = store or resolve_event_store()
    if clock is None:
        return Runtime(engines, store, specs, sinks=sinks, control=control)
    return Runtime(engines, store, specs, sinks=sinks, clock=clock, control=control)


def v1_engines(
    session_for: Callable[[str], Session] | None = None,
    workflow_for: Callable[[str], type[BaseWorkflow]] | None = None,
) -> tuple[EnginePort, ...]:
    """The engine set behind v1's public surface: agents and workflows configured the way v1
    configures them.

    Neither engine resolves anything from settings here. The workflow engine plays v1's own
    compiled graph, which carries the configured checkpointer only for a ``durable = True``
    workflow — so the ``[durability]`` extra stays optional for a project that only chats,
    and a durable workflow still resumes on the real thing.
    """
    return (V1CompatEngine(session_for), V1CompatWorkflowEngine(workflow_for))


def resolve_event_store(settings: EventsSettings | None = None) -> EventStorePort:
    """Build the event store named by ``backend``: ``memory`` (default), ``sqlite``,
    ``redis`` or ``postgres``.

    The last two are imported inside their own branch, not at module scope: this module is on
    the import path of every entry point, and Postgres needs the ``[durability]`` extra, so a
    top-level import would make that extra mandatory for anyone who only chats.
    """
    events = settings if settings is not None else get_settings().events
    backend = events.backend.strip().lower()
    if backend == "memory":
        return MemoryEventStore()
    if backend == "sqlite":
        if not events.url:
            raise ValueError("the sqlite event store needs a file path: set AGENTDECK_EVENTS_URL")
        return SqliteEventStore(events.url)
    if backend == "redis":
        if not events.url:
            raise ValueError("the redis event store needs a URL: set AGENTDECK_EVENTS_URL")
        from agentdeck.adapters.stores.redis import RedisEventStore

        return RedisEventStore(events.url)
    if backend == "postgres":
        if not events.url:
            raise ValueError("the postgres event store needs a DSN: set AGENTDECK_EVENTS_URL")
        try:
            from agentdeck.adapters.stores.postgres import PostgresEventStore
        except ImportError as exc:
            raise ImportError(
                'the postgres event store needs psycopg — install the "durability" extra: '
                'pip install "agentdeck[durability]"'
            ) from exc
        return PostgresEventStore(events.url)
    raise ValueError(f"unknown event store backend {events.backend!r}; expected memory, sqlite, redis or postgres")


__all__ = ["build_runtime", "resolve_event_store", "v1_engines"]
