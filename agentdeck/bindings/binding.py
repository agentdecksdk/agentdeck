"""``Binding`` and the endpoints it builds: one concrete protocol over one transport
(``docs/design/protocols/bindings.md``, ``spi.md``). No generic transport composition: a binding
factory (``A2A.http()``, ``ACP.stdio()``) exposes only the pairs its protocol actually supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agentdeck.bindings.gateway import ProtocolGateway

PROTOCOL_SPI_VERSION = 1
"""The contract version a :class:`Binding` declares in its own :attr:`BindingInfo.spi_version`.
Bumped on a breaking change to :class:`ProtocolGateway`, :class:`Binding`, an endpoint type or
:class:`~agentdeck.bindings.gateway.GatewayFailureCode`; an added optional field never bumps it.
"""


@dataclass(frozen=True, slots=True)
class BindingInfo:
    """What a :class:`Binding` is, for ``expose()`` to validate and a health check to report."""

    name: str
    kind: Literal["protocol", "channel", "surface"]
    transport: str
    spi_version: int
    advertises: frozenset[str]


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
    """One protocol, channel or surface over one transport. A contract, not a base class with
    behavior: what happens in :meth:`start`/:meth:`stop` belongs to the concrete binding.
    """

    info: BindingInfo

    def build(self, gateway: ProtocolGateway) -> Endpoint: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


__all__ = [
    "PROTOCOL_SPI_VERSION",
    "Binding",
    "BindingInfo",
    "Endpoint",
    "HttpEndpoint",
    "StdioEndpoint",
]
