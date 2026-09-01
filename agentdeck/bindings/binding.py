"""``Binding`` and the endpoints it builds: one protocol over one transport
(``docs/design/protocols/bindings.md``, ``spi.md``). No generic transport composition: a binding
factory (``A2A.http()``, ``ACP.stdio()``) exposes only the pairs its protocol supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentdeck.bindings.gateway import DeckGateway

PROTOCOL_SPI_VERSION = 1
"""The SPI version a :class:`Binding` declares; ``docs/design/protocols/spi.md`` holds what bumps it."""


@dataclass(frozen=True, slots=True)
class BindingInfo:
    """What a :class:`Binding` is, for ``expose()`` to validate and a health check to report."""

    name: str
    kind: Literal["protocol", "channel", "surface"]
    transport: str
    spi_version: int
    advertises: frozenset[str]
    """Capability names this binding claims (``streaming``, ``hitl``, ``control.cancel``, ...);
    the SPI contract suite holds a binding to each one."""

    requires: frozenset[str] = frozenset()
    """Other bindings, by :attr:`name`, this one needs in the same exposure."""


@dataclass(frozen=True, slots=True)
class HttpEndpoint:
    """An isolated ASGI app or router, mounted on the shared listener at ``path``."""

    path: str
    app: Any


@dataclass(frozen=True, slots=True)
class StdioEndpoint:
    """A coroutine over stdin/stdout, run as a task with no port opened."""

    run: Callable[[], Awaitable[None]]


Endpoint = HttpEndpoint | StdioEndpoint


class Binding(Protocol):
    """One protocol, channel or surface over one transport."""

    info: BindingInfo

    def build(self, gateway: DeckGateway) -> Endpoint:
        """Build and validate the endpoint. No I/O: nothing opens here."""
        ...

    async def start(self) -> None:
        """Start binding-owned resources; the Exposure owns any task spawned here."""
        ...

    async def stop(self) -> None:
        """Stop binding-owned resources. Idempotent: may run without a preceding start."""
        ...


__all__ = [
    "PROTOCOL_SPI_VERSION",
    "Binding",
    "BindingInfo",
    "Endpoint",
    "HttpEndpoint",
    "StdioEndpoint",
]
