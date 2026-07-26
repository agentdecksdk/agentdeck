"""Resolve a LangGraph checkpointer for ``durable=True`` workflows from settings.

Mirrors ``runtime.observability``'s degrade-gracefully shape: ``memory`` ships with
core ``langgraph`` and needs nothing extra; ``sqlite`` / ``postgres`` live in the
optional ``[durability]`` extra (``langgraph-checkpoint-sqlite`` /
``langgraph-checkpoint-postgres``) and are imported lazily, only when actually
requested, with a clear install hint if the extra is missing.

Connection lifecycle is intentionally minimal for now: one saver per process,
cached by backend+url so repeated ``BaseWorkflow.build()`` calls (and repeated
test runs against the same sqlite file) reuse the same connection instead of
opening a new one per compile. Wiring this into ``App``'s lifecycle (closing the
connection on shutdown) is follow-up work once issue #1 (App lifecycle) lands —
see PR notes.
"""

from __future__ import annotations

import asyncio
import threading
from functools import cache
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from langgraph.checkpoint.base import BaseCheckpointSaver

    from agentdeck.runtime.settings import CheckpointSettings

_DURABILITY_HINT = 'install the "durability" extra: pip install "agentdeck[durability]"'

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[None, None, _T]) -> _T:
    """Run ``coro`` to completion, whether or not an event loop is already running.

    ``BaseWorkflow.build()`` resolves the checkpointer synchronously, but it can be
    invoked lazily from *inside* an async ``run()`` (first call not pre-built) — so
    plain ``asyncio.run`` would collide with the running loop. The one-shot bootstrap
    connection (aiosqlite's async handshake) is cheap enough to hand to a throwaway
    thread+loop in that case; all later query traffic runs on the caller's own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    result: list[_T] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001 — re-raised on the calling thread below
            error.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def resolve_checkpointer(settings: CheckpointSettings) -> BaseCheckpointSaver:
    """Build the checkpointer named by ``settings.backend``.

    Raises ``ValueError`` for an unknown backend and ``ImportError`` (with an
    install hint) when ``sqlite``/``postgres`` is requested but the
    ``[durability]`` extra isn't installed.
    """
    backend = settings.backend.strip().lower()
    if backend == "memory":
        return _memory_saver()
    if backend == "sqlite":
        return _sqlite_saver(settings.url)
    if backend == "postgres":
        return _postgres_saver(settings.url)
    raise ValueError(f"unknown checkpoint backend {settings.backend!r}; expected sqlite, postgres, or memory")


@cache
def _memory_saver() -> BaseCheckpointSaver:
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@cache
def _sqlite_saver(url: str) -> BaseCheckpointSaver:
    """Cached ``AsyncSqliteSaver`` for ``url`` — one connection for the process lifetime.

    Note: the saver holds an internal ``asyncio.Lock`` that binds to whichever event
    loop first acquires it. That's a non-issue for the intended shape (one
    long-lived loop per process — a server, or a single top-level ``asyncio.run``),
    but a script that calls ``asyncio.run()`` more than once against the *same*
    durable workflow class in one process will hit "Lock ... bound to a different
    event loop" on the second call, because ``BaseWorkflow._compiled`` also caches
    the compiled graph (and the checkpointer baked into it) for the class's
    lifetime. Rebuilding both per-loop is future work if that pattern shows up.
    """
    try:
        import aiosqlite  # ty: ignore[unresolved-import] — [durability] extra
        from langgraph.checkpoint.sqlite import aio as sqlite_aio  # ty: ignore[unresolved-import] — [durability] extra
    except ImportError as exc:
        raise ImportError(
            f"checkpoint backend 'sqlite' needs langgraph-checkpoint-sqlite — {_DURABILITY_HINT}"
        ) from exc

    # The plain (sync) ``SqliteSaver`` can't back ``graph.ainvoke`` — it raises
    # NotImplementedError on every async method. The async saver needs its aiosqlite
    # handshake awaited once; ``_run_sync`` does that whether or not we're already
    # inside the caller's event loop (see its docstring).
    path = url or ".agentdeck/checkpoints.sqlite3"

    async def _connect() -> aiosqlite.Connection:
        conn = aiosqlite.connect(path)
        await conn
        return conn

    conn = _run_sync(_connect())
    saver = sqlite_aio.AsyncSqliteSaver(conn)
    _run_sync(saver.setup())
    return saver


@cache
def _postgres_saver(url: str) -> BaseCheckpointSaver:
    if not url:
        raise ValueError("checkpoint backend 'postgres' needs AGENTDECK_CHECKPOINT_URL (a DSN)")
    try:
        from langgraph.checkpoint.postgres.aio import (  # ty: ignore[unresolved-import] — [durability] extra
            AsyncPostgresSaver,
        )
    except ImportError as exc:
        raise ImportError(
            f"checkpoint backend 'postgres' needs langgraph-checkpoint-postgres — {_DURABILITY_HINT}",
        ) from exc

    # Async saver, same reason as sqlite: the runner always calls ``ainvoke``.
    # ``from_conn_string`` is an async contextmanager owning the connection; we enter
    # it manually and keep the saver for the process lifetime (see module docstring).
    saver: Any = _run_sync(AsyncPostgresSaver.from_conn_string(url).__aenter__())
    _run_sync(saver.setup())
    return saver


__all__ = ["resolve_checkpointer"]
