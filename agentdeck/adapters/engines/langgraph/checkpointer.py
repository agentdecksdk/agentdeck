"""Resolve a LangGraph checkpointer for the langgraph engine's runs.

Relocated from ``agentdeck.runtime.checkpointer``, which was written for v1's
``BaseWorkflow`` durability but holds exactly the state a checkpointer engine must keep
private to its own adapter (ADR-D5: execution state belongs to the engine that produced
it, never shared or derived by an outer ring) — the same relationship ``sessions.py`` has
to the openai-agents adapter. ``agentdeck.runtime.checkpointer`` is now a thin re-export
so ``agentdeck.workflows.base`` (v1, frozen behavior) keeps working unchanged; nothing new
imports that path. ``memory`` ships with core ``langgraph`` and needs nothing extra;
``sqlite`` / ``postgres`` live in the optional ``[durability]`` extra
(``langgraph-checkpoint-sqlite`` / ``langgraph-checkpoint-postgres``) and are imported
lazily, only when actually requested, with a clear install hint if the extra is missing.

Connection lifecycle is intentionally minimal for now: one saver per process, cached by
backend+url so repeated calls against the same file reuse the same connection instead of
opening a new one per compile.
"""

from __future__ import annotations

import asyncio
import threading
from functools import cache
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from langgraph.checkpoint.base import BaseCheckpointSaver

_DURABILITY_HINT = 'install the "durability" extra: pip install "agentdeck[durability]"'

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[None, None, _T]) -> _T:
    """Run ``coro`` to completion, whether or not an event loop is already running.

    The engine may resolve a checkpointer lazily from *inside* an async ``start()`` call,
    so plain ``asyncio.run`` would collide with the running loop. The one-shot bootstrap
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


def resolve_checkpointer(backend: str, url: str = "") -> BaseCheckpointSaver:
    """Build the checkpointer named by ``backend`` (``memory`` / ``sqlite`` / ``postgres``).

    ``url`` is the sqlite file path or the Postgres DSN — primitives, not a settings
    object, so this adapter takes core plus langgraph and nothing else. Raises
    ``ValueError`` for an unknown backend and ``ImportError`` (with an install hint) when
    ``sqlite``/``postgres`` is requested but the ``[durability]`` extra isn't installed.
    """
    normalized = backend.strip().lower()
    if normalized == "memory":
        return _memory_saver()
    if normalized == "sqlite":
        return _sqlite_saver(url)
    if normalized == "postgres":
        return _postgres_saver(url)
    raise ValueError(f"unknown checkpoint backend {backend!r}; expected sqlite, postgres, or memory")


@cache
def _memory_saver() -> BaseCheckpointSaver:
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


@cache
def _sqlite_saver(url: str) -> BaseCheckpointSaver:
    """Cached ``AsyncSqliteSaver`` for ``url`` — one connection for the process lifetime.

    Note: the saver holds an internal ``asyncio.Lock`` that binds to whichever event loop
    first acquires it. That's a non-issue for the intended shape (one long-lived loop per
    process — a server, or a single top-level ``asyncio.run``), but a script that calls
    ``asyncio.run()`` more than once against the same durable graph in one process will hit
    "Lock ... bound to a different event loop" on the second call. Rebuilding per-loop is
    future work if that pattern shows up.
    """
    try:
        import aiosqlite  # ty: ignore[unresolved-import] — [durability] extra
        from langgraph.checkpoint.sqlite import aio as sqlite_aio  # ty: ignore[unresolved-import] — [durability] extra
    except ImportError as exc:
        raise ImportError(
            f"checkpoint backend 'sqlite' needs langgraph-checkpoint-sqlite — {_DURABILITY_HINT}"
        ) from exc

    # AsyncSqliteSaver.__init__ needs a running loop — build it inside _run_sync, matching postgres.
    path = url or ".agentdeck/checkpoints.sqlite3"

    async def _connect_and_build() -> BaseCheckpointSaver:
        conn = aiosqlite.connect(path)
        # aiosqlite runs a background worker thread per connection, non-daemon by
        # default; a process that exits normally (not killed) hangs forever joining it
        # since this connection is cached for the process lifetime and never closed.
        conn._thread.daemon = True  # noqa: SLF001 — aiosqlite exposes no public way to set this
        await conn
        saver = sqlite_aio.AsyncSqliteSaver(conn)
        await saver.setup()
        return saver

    saver: Any = _run_sync(_connect_and_build())
    return saver


@cache
def _postgres_saver(url: str) -> BaseCheckpointSaver:
    if not url:
        raise ValueError("checkpoint backend 'postgres' needs a DSN")
    try:
        from langgraph.checkpoint.postgres.aio import (  # ty: ignore[unresolved-import] — [durability] extra
            AsyncPostgresSaver,
        )
    except ImportError as exc:
        raise ImportError(
            f"checkpoint backend 'postgres' needs langgraph-checkpoint-postgres — {_DURABILITY_HINT}",
        ) from exc

    # Async saver, same reason as sqlite: the engine always calls ``ainvoke``/``astream``.
    # ``from_conn_string`` is an async contextmanager owning the connection; we enter it
    # manually and cache the saver, one connection for the process lifetime.
    saver: Any = _run_sync(AsyncPostgresSaver.from_conn_string(url).__aenter__())
    _run_sync(saver.setup())
    return saver


__all__ = ["resolve_checkpointer"]
