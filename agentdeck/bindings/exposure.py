"""``Exposure``: validates a set of bindings, then hosts and owns their lifecycle
(``docs/design/protocols/exposure.md``).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.bindings.binding import PROTOCOL_SPI_VERSION, HttpEndpoint, StdioEndpoint
from agentdeck.bindings.gateway import DeckGateway
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agentdeck.bindings.binding import Binding, Endpoint
    from agentdeck.deck import Deck


class Exposure:
    """Hosts and owns a validated set of bindings."""

    def __init__(self, deck: Deck, bindings: Sequence[Binding]) -> None:
        bindings = tuple(bindings)
        _validate_info(bindings)
        gateway = DeckGateway(deck)
        endpoints = [binding.build(gateway) for binding in bindings]
        _validate_endpoints(bindings, endpoints)
        self._deck = deck
        self._bindings = bindings
        self._http = [endpoint for endpoint in endpoints if isinstance(endpoint, HttpEndpoint)]
        self._stdio = next((e for e in endpoints if isinstance(e, StdioEndpoint)), None)

    @asynccontextmanager
    async def _lifecycle(self) -> AsyncIterator[asyncio.Future[None] | None]:
        owns_deck = not self._deck.is_open
        if owns_deck:
            await self._deck.__aenter__()
        started: list[Binding] = []
        stdio_task: asyncio.Future[None] | None = None
        first_error: BaseException | None = None
        try:
            for binding in self._bindings:
                await binding.start()
                started.append(binding)
            if self._stdio is not None:
                stdio_task = asyncio.ensure_future(self._stdio.run())
            yield stdio_task
        except BaseException as error:
            first_error = error
        finally:
            # Every step in its own try: one failure must not skip the rest, and the error the
            # caller sees is the first one, not whatever failed last on the way out.
            if stdio_task is not None:
                stdio_task.cancel()
                try:
                    await stdio_task
                except asyncio.CancelledError:
                    pass
                except BaseException as error:
                    first_error = first_error or error
            for binding in reversed(started):
                try:
                    await binding.stop()
                except BaseException as error:
                    first_error = first_error or error
            if owns_deck:
                try:
                    await self._deck.aclose()
                except BaseException as error:
                    first_error = first_error or error
            if first_error is not None:
                raise first_error

    def asgi(self) -> Any:
        """Build the shared ASGI application, with this exposure's lifecycle as its lifespan."""
        from starlette.applications import Starlette
        from starlette.routing import Mount

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with self._lifecycle():
                yield

        # A Mount matches by prefix and Starlette takes the first match, so "/api" ahead of
        # "/api/admin" would swallow it: deepest path first.
        ordered = sorted(self._http, key=lambda endpoint: _depth(endpoint.path), reverse=True)
        root = next((e for e in ordered if _normalize(e.path) == "/"), None)
        app = Starlette(
            routes=[Mount(_normalize(e.path), app=e.app) for e in ordered if e is not root],
            lifespan=lifespan,
        )
        if root is not None:
            # Not a Mount: `Mount("/")` matches everything, and a bare "/a2a" (no trailing slash,
            # which no Mount pattern matches) would reach the root app instead of redirecting.
            app.router.default = root.app
        return app

    async def serve(self, *, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Serve the exposure: stdio alone runs the lifecycle directly, HTTP runs under uvicorn."""
        if not self._http:
            async with self._lifecycle() as stdio_task:
                if stdio_task is not None:
                    await stdio_task
            return
        import uvicorn

        config = uvicorn.Config(self.asgi(), host=host, port=port)
        await uvicorn.Server(config).serve()


def _normalize(path: str) -> str:
    """One spelling per path, so ``/a2a`` and ``/a2a/`` cannot both be claimed."""
    return "/" + path.strip("/")


def _depth(path: str) -> int:
    return len([segment for segment in _normalize(path).split("/") if segment])


def _validate_info(bindings: Sequence[Binding]) -> None:
    counts = Counter(binding.info.name for binding in bindings)
    if repeated := sorted(name for name, count in counts.items() if count > 1):
        raise ConfigError(
            f"binding names must be unique in one exposure, but {repeated} appear more than once. "
            f"`requires` resolves by name, so give each binding its own `BindingInfo.name`."
        )
    names = set(counts)
    for binding in bindings:
        info = binding.info
        if info.spi_version != PROTOCOL_SPI_VERSION:
            raise ConfigError(
                f"binding {info.name!r} declares spi_version={info.spi_version}, but this "
                f"AgentDeck supports spi_version={PROTOCOL_SPI_VERSION}."
            )
        if missing := sorted(info.requires - names):
            raise ConfigError(
                f"binding {info.name!r} requires {missing}, not in this exposure. Available: {sorted(names)}."
            )


def _validate_endpoints(bindings: Sequence[Binding], endpoints: Sequence[Endpoint]) -> None:
    owner_of_path: dict[str, str] = {}
    stdio_owners: list[str] = []
    for binding, endpoint in zip(bindings, endpoints, strict=True):
        if isinstance(endpoint, HttpEndpoint):
            path = _normalize(endpoint.path)
            if owner := owner_of_path.get(path):
                raise ConfigError(f"HTTP path {path!r} is claimed by both {owner!r} and {binding.info.name!r}.")
            owner_of_path[path] = binding.info.name
        elif isinstance(endpoint, StdioEndpoint):
            stdio_owners.append(binding.info.name)
    if len(stdio_owners) > 1:
        raise ConfigError(f"only one stdio binding is allowed per exposure; got {sorted(stdio_owners)}.")


__all__ = ["Exposure"]
