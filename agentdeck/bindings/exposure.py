"""``Exposure``: validates a set of bindings, then hosts and owns their lifecycle
(``docs/design/protocols/exposure.md``).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from agentdeck.bindings.binding import PROTOCOL_SPI_VERSION, HttpEndpoint, StdioEndpoint
from agentdeck.bindings.gateway import ProtocolGateway
from agentdeck.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agentdeck.bindings.binding import Binding, Endpoint
    from agentdeck.deck import Deck


class Exposure:
    """Owns the lifecycle of the bindings validated at construction. Never inspects an
    :class:`~agentdeck.core.events.Event`: hosting and rollback only, no protocol semantics.
    """

    def __init__(self, deck: Deck, bindings: Sequence[Binding]) -> None:
        bindings = tuple(bindings)
        _validate_info(bindings)
        # Both pure (build() by contract, ProtocolGateway.__init__ only stores deck), so both
        # run here, ahead of exposure.md's "open Deck -> build gateway" order.
        gateway = ProtocolGateway(deck)
        endpoints = [binding.build(gateway) for binding in bindings]
        _validate_endpoints(bindings, endpoints)
        self._deck = deck
        self._bindings = bindings
        self._http_endpoints = [e for e in endpoints if isinstance(e, HttpEndpoint)]
        self._stdio_endpoint = next((e for e in endpoints if isinstance(e, StdioEndpoint)), None)

    @asynccontextmanager
    async def _lifecycle(self) -> AsyncIterator[asyncio.Future[None] | None]:
        """``started`` holds only bindings whose ``start()`` returned, so a failure on binding N
        stops 1..N-1 and never N; shutdown steps each run in their own ``try`` so none is skipped."""
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
            if self._stdio_endpoint is not None:
                stdio_task = asyncio.ensure_future(self._stdio_endpoint.run())
            yield stdio_task
        except BaseException as error:
            first_error = error
        finally:
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
                await self._deck.aclose()
            if first_error is not None:
                raise first_error

    def asgi(self) -> Any:
        """One ``Starlette`` app with one ``Mount`` per :class:`~agentdeck.bindings.binding.HttpEndpoint`,
        for embedding in a host that owns the server. Its lifespan is the exposure's lifecycle:
        on startup it opens the Deck only if the Deck is not already open, then calls each
        binding's ``start()`` in order; on shutdown it calls ``stop()`` in reverse, then closes the
        Deck only if this exposure opened it. A ``start()`` failure stops the bindings already
        started and re-raises; the first exception wins, and every shutdown step still runs.
        """
        from starlette.applications import Starlette
        from starlette.routing import Mount

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with self._lifecycle():
                yield

        routes = [Mount(endpoint.path, app=endpoint.app) for endpoint in self._http_endpoints]
        return Starlette(routes=routes, lifespan=lifespan)

    async def serve(self, *, host: str = "0.0.0.0", port: int = 8000) -> None:
        """A stdio-only exposure never imports uvicorn or binds a port: it runs the lifecycle
        directly and waits for the stdio endpoint to finish. Any
        :class:`~agentdeck.bindings.binding.HttpEndpoint` routes this through :meth:`asgi` and
        uvicorn instead, whose own signal handling already runs ``stop()`` in reverse on Ctrl-C.
        """
        if not self._http_endpoints:
            async with self._lifecycle() as stdio_task:
                if stdio_task is not None:
                    await stdio_task
            return
        import uvicorn

        config = uvicorn.Config(self.asgi(), host=host, port=port)
        await uvicorn.Server(config).serve()


def _validate_info(bindings: Sequence[Binding]) -> None:
    names = {binding.info.name for binding in bindings}
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
        if unimplemented := sorted(info.advertises - info.projects):
            raise ConfigError(f"binding {info.name!r} advertises {unimplemented} with no projection implemented.")


def _validate_endpoints(bindings: Sequence[Binding], endpoints: Sequence[Endpoint]) -> None:
    owner_of_path: dict[str, str] = {}
    stdio_owners: list[str] = []
    for binding, endpoint in zip(bindings, endpoints, strict=True):
        if isinstance(endpoint, HttpEndpoint):
            if owner := owner_of_path.get(endpoint.path):
                raise ConfigError(
                    f"HTTP path {endpoint.path!r} is claimed by both {owner!r} and {binding.info.name!r}."
                )
            owner_of_path[endpoint.path] = binding.info.name
        elif isinstance(endpoint, StdioEndpoint):
            stdio_owners.append(binding.info.name)
    if len(stdio_owners) > 1:
        raise ConfigError(f"only one stdio binding is allowed per exposure; got {sorted(stdio_owners)}.")


__all__ = ["Exposure"]
