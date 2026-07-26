"""``MCPServerStreamableHttp`` hardened for real deployments.

Adds what the SDK leaves out: connect-time retry, and — after an MCP server
restart drops our session id — detection of the resulting mid-session 404 plus
a transparent re-``initialize`` + replay, so tool calls self-heal without a
harness restart.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, suppress
from typing import TYPE_CHECKING, Any, TypeVar

import httpx
from agents.mcp import MCPServerStreamableHttp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class MCPServerStreamableHttpResilient(MCPServerStreamableHttp):
    """HTTP MCP client that retries connect and recovers a dropped session.

    Recovery splits by whether the connection is still there:

    * A live-connection hiccup (read timeout, protocol blip, 5xx) is retried on
      the **same session** — cheap, no rebuild (SDK ``max_retry_attempts``).
    * A **gone** connection — a dropped session id (404) or an unreachable server
      (:meth:`_connection_gone`) — skips that retry and escalates at once to a
      fresh ``connect()`` + replay (:meth:`_with_recovery`), the only thing that
      fixes a new-session-needed restart or a torn-down stream. Connect itself
      retries transient errors; auth / bad-URL 404 stay fatal (:meth:`_is_transient`).

    The SDK wraps transport failures in ``UserError`` (``__cause__`` is the real
    error), so the predicates walk the cause chain.
    """

    TRANSIENT_HTTPX_EXCEPTIONS: tuple[type[BaseException], ...] = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    )
    # The connection itself is unavailable — a same-session retry can't help, only a
    # reconnect can. (``ReadTimeout``/``RemoteProtocolError`` are NOT here: the
    # connection is alive, the request just hiccuped, so retrying it is worth it.)
    CONNECTION_GONE_EXCEPTIONS: tuple[type[BaseException], ...] = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
    )
    FATAL_STATUS_CODES: frozenset[int] = frozenset({401, 403, 404})
    # A mid-session 404 means a dropped session id (restart), not a bad URL.
    SESSION_LOST_STATUS_CODES: frozenset[int] = frozenset({404})

    def __init__(
        self,
        *args: Any,
        connect_max_attempts: int = 3,
        connect_backoff_base_seconds: float = 1.0,
        max_retry_attempts: int = 5,  # same-session retry for live hiccups; gone conns skip it
        retry_backoff_seconds_base: float = 1.0,
        reconnect_max_attempts: int = 2,
        reconnect_backoff_base_seconds: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args,
            max_retry_attempts=max_retry_attempts,
            retry_backoff_seconds_base=retry_backoff_seconds_base,
            **kwargs,
        )
        self.connect_max_attempts = connect_max_attempts
        self.connect_backoff_base_seconds = connect_backoff_base_seconds
        self.reconnect_max_attempts = reconnect_max_attempts
        self.reconnect_backoff_base_seconds = reconnect_backoff_base_seconds

    async def connect(self) -> None:
        last_exc: BaseException | None = None
        for attempt in range(1, self.connect_max_attempts + 1):
            try:
                await super().connect()
                if attempt > 1:
                    logger.info("MCP connect succeeded for %s on attempt %d", self.name, attempt)
                return
            except BaseException as exc:
                last_exc = exc
                if not self._is_transient(exc):
                    raise
            backoff = self.connect_backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "MCP connect to %s failed (attempt %d/%d) — retrying in %.1fs",
                self.name,
                attempt,
                self.connect_max_attempts,
                backoff,
            )
            await asyncio.sleep(backoff)
        if last_exc is None:  # only reachable if the loop ran zero times (attempts < 1)
            raise RuntimeError(f"MCP connect to {self.name} made no attempt")
        raise last_exc

    @classmethod
    def _is_transient(cls, exc: BaseException) -> bool:
        """Retryable connect-time transport error? Walks the cause chain."""
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, httpx.HTTPStatusError):
                return current.response.status_code not in cls.FATAL_STATUS_CODES
            if isinstance(current, cls.TRANSIENT_HTTPX_EXCEPTIONS):
                return True
            current = current.__cause__ or current.__context__
        return False

    @classmethod
    def _is_session_lost(cls, exc: BaseException) -> bool:
        """Mid-session 404 (server dropped our session id)? Walks the cause chain."""
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, httpx.HTTPStatusError):
                return current.response.status_code in cls.SESSION_LOST_STATUS_CODES
            current = current.__cause__ or current.__context__
        return False

    @classmethod
    def _connection_gone(cls, exc: BaseException) -> bool:
        """Connection unavailable — dropped session id (404) or unreachable server.

        These must NOT be retried on the same session (there's nothing live to retry);
        recovery reconnects instead. A live-connection hiccup returns False.
        """
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, httpx.HTTPStatusError):
                if current.response.status_code in cls.SESSION_LOST_STATUS_CODES:
                    return True
            elif isinstance(current, cls.CONNECTION_GONE_EXCEPTIONS):
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _run_with_retries(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Same-session retry for live hiccups; skip it when the connection is gone.

        A dead session / unreachable server (:meth:`_connection_gone`) surfaces at
        once so :meth:`_with_recovery` reconnects instead of retrying a connection
        that isn't there. Everything else keeps the SDK's backoff policy.
        """
        attempts = 0
        while True:
            try:
                return await func()
            except Exception as exc:
                if self._connection_gone(exc):
                    raise
                attempts += 1
                if self.max_retry_attempts != -1 and attempts > self.max_retry_attempts:
                    raise
                await asyncio.sleep(self.retry_backoff_seconds_base * (2 ** (attempts - 1)))

    async def _reconnect(self) -> None:
        """Drop the stale session and re-run ``initialize`` for a fresh session id."""
        with suppress(Exception):  # dead transport teardown is best-effort
            await self.cleanup()  # aclose()s the old streams and nulls self.session
        self.exit_stack = AsyncExitStack()  # cleanup aclosed the old one
        await self.connect()

    async def _with_recovery(self, call: Callable[[], Awaitable[_T]]) -> _T:
        """Run ``call``; only when the connection is *gone* re-initialize and replay.

        Bounded + backed off. Fires on :meth:`_connection_gone` (dropped session id or
        unreachable server) — a server restart or a torn-down stream. A live-connection
        hiccup is NOT recovered here; it already had its same-session retry in
        :meth:`_run_with_retries` and is re-raised.
        """
        try:
            return await call()
        except BaseException as exc:
            if not self._connection_gone(exc):
                raise
        last: BaseException | None = None
        for attempt in range(1, self.reconnect_max_attempts + 1):
            logger.warning(
                "MCP %s: connection lost (server restart/outage?) — re-initializing (attempt %d/%d)",
                self.name,
                attempt,
                self.reconnect_max_attempts,
            )
            try:
                await self._reconnect()
                result = await call()
            except BaseException as exc:
                last = exc
                if not self._connection_gone(exc):
                    raise
            else:
                logger.info("MCP %s: connection recovered on attempt %d", self.name, attempt)
                return result
            await asyncio.sleep(self.reconnect_backoff_base_seconds * (2 ** (attempt - 1)))
        if last is None:  # only reachable if reconnect_max_attempts < 1
            raise RuntimeError(f"MCP {self.name} recovery made no attempt")
        logger.error("MCP %s: connection recovery failed after %d attempts", self.name, self.reconnect_max_attempts)
        raise last

    # ``run_context`` / ``agent`` / return types are the SDK's; typed ``Any`` here so
    # these thin recovery wrappers need no import block just to restate them.
    async def list_tools(self, run_context: Any = None, agent: Any = None) -> Any:
        return await self._with_recovery(
            lambda: super(MCPServerStreamableHttpResilient, self).list_tools(run_context, agent)
        )

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Any:
        return await self._with_recovery(
            lambda: super(MCPServerStreamableHttpResilient, self).call_tool(tool_name, arguments, meta)
        )


__all__ = ["MCPServerStreamableHttpResilient"]
