"""The composition root: the one place adapters are built and handed to a ``Runtime``.

Everything above this module takes ports; everything below it is an adapter. ``App`` calls
:func:`build_runtime` and so does every other entry point that needs a real Runtime — the
demo script, the compat surface's tests — so a Runtime is assembled the same way
everywhere instead of hand-wired per caller. A second front door (a code-first ``Deck()``)
becomes another caller of this function rather than a second assembly.

Only the parts a caller actually varies are arguments; the rest resolve from settings. That
resolution happens *here*, never inside an adapter: an engine that reached for
``get_settings()`` itself could not be handed a different endpoint by a caller, and a second
front door would have to mutate process state to get one. The ``resolve_*`` functions below
are what an entry point calls to fill an adapter's constructor in.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentdeck.adapters.control.memory import MemoryControlPort
from agentdeck.adapters.control.sqlite import SqliteControlPort
from agentdeck.adapters.engines.openai_agents.runconfig import RunSettings
from agentdeck.adapters.stores.memory import MemoryEventStore
from agentdeck.adapters.stores.sqlite import SqliteEventStore
from agentdeck.runtime.discovery import InvocableRegistry
from agentdeck.runtime.service import Runtime
from agentdeck.runtime.settings import (
    ControlSettings,
    EventsSettings,
    Settings,
    default_use_responses,
    get_settings,
    parse_backend_url,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta

    from agentdeck.core.invocable import InvocableSpec
    from agentdeck.core.ports import ControlPort, EnginePort, EventSinkPort, EventStorePort

logger = logging.getLogger(__name__)


def build_runtime(
    *,
    engines: Sequence[EnginePort],
    invocables: Mapping[str, InvocableSpec] | None = None,
    store: EventStorePort | None = None,
    sinks: Sequence[EventSinkPort] = (),
    control: ControlPort | None = None,
    stale_run_after: timedelta | None = None,
) -> Runtime:
    """Wire ``engines`` into a Runtime over the project's invocables.

    ``invocables`` defaults to discovery over ``./.agentdeck`` — pass a mapping to run
    specs built in code instead. ``store`` defaults to the configured event store,
    ``control`` to the configured control port, and ``stale_run_after`` to
    ``RuntimeSettings.stale_run_after``.

    ``sinks`` defaults to none, and telemetry in particular is *not* resolved here. A sink can
    hold a live client with background threads, and resolving one while a Runtime is assembled
    built that client before anyone had said whether they wanted it — the ordering behind #162.
    :func:`resolve_observers` is what reads settings, and ``Deck.__aenter__`` is what calls it,
    once, as it opens.

    Timestamps are assigned by the store, in the same write that persists the event
    (ADR-D11), so holding time means building the store with a clock —
    ``MemoryEventStore(clock=...)``, ``RedisEventStore(clock=...)`` — and the two SQL stores
    read their backend's clock so that N workers on one database compare one clock rather
    than N.
    """
    engines = tuple(engines)
    specs = InvocableRegistry(engines).load() if invocables is None else invocables
    store = store or resolve_event_store()
    control = control or resolve_control_port()
    if stale_run_after is None:
        stale_run_after = get_settings().runtime.stale_run_after
    return Runtime(engines, store, specs, sinks=sinks, control=control, stale_run_after=stale_run_after)


def resolve_observers() -> tuple[EventSinkPort, ...]:
    """The observers a Deck opens when its caller named none: Langfuse, if configured.

    Nothing is built here either — :class:`~agentdeck.observers.Langfuse` reads its settings
    and constructs its client in ``start()``, which the Deck calls as it opens. This function
    only answers "did the environment ask for one?", so it stays as free of network and of the
    optional ``[observability]`` extra as the rest of ``build()``'s path.

    One observer today, and the shape stays plural: the Runtime already fans out to as many
    taps as it is given, and a caller with a cost or audit observer of its own passes
    ``observers=`` to ``Deck`` instead of coming through here.
    """
    from agentdeck.observers import Langfuse

    return (Langfuse(),) if get_settings().langfuse.enabled else ()


def resolve_run_settings(settings: Settings | None = None) -> RunSettings:
    """Everything one agent run is configured with, read out of settings once.

    Every field here is invisible to a fake-model test suite and decisive against a real
    endpoint — the CA bundle, the token cap, the provider's own base URL — which is why
    ``tests/test_run_config_parity.py`` compares the resolved result field by field rather
    than trusting a run that streamed to have been configured correctly.
    """
    resolved = settings if settings is not None else get_settings()
    return RunSettings(
        model=resolved.openai.model,
        api_key=resolved.openai.api_key,
        base_url=resolved.openai.base_url,
        ca_bundle=resolved.openai.ca_bundle,
        use_responses=default_use_responses(),
        workflow_name=resolved.runner.workflow_name,
        nest_handoff_history=True,
        temperature=resolved.runner.temperature,
        max_tokens=resolved.runner.max_tokens,
        max_turns=resolved.runner.max_turns,
    )


def resolve_checkpoint(settings: Settings | None = None) -> tuple[str, str]:
    """The ``(backend, path_or_dsn)`` a durable workflow checkpoints to, derived from
    ``AGENTDECK_CHECKPOINT``'s scheme.

    A pair of strings, not a saver: the postgres saver lives in the ``[durability]`` extra, so
    naming a backend here must not import one — the langgraph adapter builds it at the first
    durable run and not before. ``postgresql`` normalizes to the backend name
    ``resolve_checkpointer`` expects (``postgres``); sqlite's own value is the bare path after
    the scheme, since the saver takes a filesystem path, not a URL.
    """
    checkpoint = (settings if settings is not None else get_settings()).checkpoint
    scheme, rest = parse_backend_url(checkpoint.url)
    backend = "postgres" if scheme == "postgresql" else scheme
    return backend, rest if backend == "sqlite" else checkpoint.url


def resolve_control_port(settings: ControlSettings | None = None) -> ControlPort:
    """Build the control port named by ``AGENTDECK_CONTROL``'s scheme: ``memory://`` (default)
    or ``sqlite://<path>``.

    Always built, never left off: a Runtime without one cannot pause or cancel anything, and a
    caller finding that out from an endpoint that silently did nothing is worse than the
    in-memory port's own limit — which is that only this process can reach the run.
    """
    control = settings if settings is not None else get_settings().control
    scheme, rest = parse_backend_url(control.url)
    if scheme == "memory":
        logger.warning(
            "AGENTDECK_CONTROL is 'memory://': a signal written in one process is invisible to another — "
            "'agentdeck runs signal' and a second worker cannot reach a run. Set AGENTDECK_CONTROL=sqlite:///<path> "
            "to cross process boundaries."
        )
        return MemoryControlPort()
    if scheme == "sqlite":
        if not rest:
            raise ValueError("the sqlite control port needs a file path: set AGENTDECK_CONTROL=sqlite:///<path>")
        return SqliteControlPort(rest)
    raise ValueError(
        f"unknown control backend {scheme!r} in AGENTDECK_CONTROL={control.url!r}; expected memory or sqlite"
    )


def resolve_event_store(settings: EventsSettings | None = None) -> EventStorePort:
    """Build the event store named by ``AGENTDECK_EVENTS``'s scheme: ``memory://`` (default),
    ``sqlite://<path>``, ``redis://``/``rediss://<url>``, or ``postgresql://<dsn>``.

    The last two are imported inside their own branch, not at module scope: this module is on
    the import path of every entry point, and Postgres needs the ``[durability]`` extra and
    Redis the ``[redis]`` extra, so a top-level import would make either mandatory for anyone
    who only chats.
    """
    events = settings if settings is not None else get_settings().events
    scheme, rest = parse_backend_url(events.url)
    if scheme == "memory":
        logger.warning(
            "AGENTDECK_EVENTS is 'memory://': the event log never evicts and is lost on restart. Set "
            "AGENTDECK_EVENTS=sqlite:///<path> for a durable log, or redis://.../postgresql://... for one "
            "several workers can share."
        )
        return MemoryEventStore()
    if scheme == "sqlite":
        if not rest:
            raise ValueError("the sqlite event store needs a file path: set AGENTDECK_EVENTS=sqlite:///<path>")
        return SqliteEventStore(rest)
    if scheme in ("redis", "rediss"):
        try:
            from agentdeck.adapters.stores.redis import RedisEventStore
        except ImportError as exc:
            raise ImportError(
                'the redis event store needs the redis client — install the "redis" extra: '
                'pip install "agentdeck-sdk[redis]"'
            ) from exc
        return RedisEventStore(events.url)
    if scheme in ("postgres", "postgresql"):
        try:
            from agentdeck.adapters.stores.postgres import PostgresEventStore
        except ImportError as exc:
            raise ImportError(
                'the postgres event store needs psycopg — install the "durability" extra: '
                'pip install "agentdeck[durability]"'
            ) from exc
        return PostgresEventStore(events.url)
    raise ValueError(
        f"unknown event store scheme {scheme!r} in AGENTDECK_EVENTS={events.url!r}; expected memory, sqlite, "
        "redis, rediss, or postgresql"
    )


__all__ = [
    "build_runtime",
    "resolve_checkpoint",
    "resolve_control_port",
    "resolve_event_store",
    "resolve_observers",
    "resolve_run_settings",
]
