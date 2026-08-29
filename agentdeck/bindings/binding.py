"""``Binding`` and the endpoints it builds: one concrete protocol over one transport
(``docs/design/protocols/bindings.md``, ``spi.md``). No generic transport composition: a binding
factory (``A2A.http()``, ``ACP.stdio()``) exposes only the pairs its protocol actually supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from agentdeck.bindings.gateway import DeckGateway

PROTOCOL_SPI_VERSION = 1
"""The contract version a :class:`Binding` declares in its own :attr:`BindingInfo.spi_version`.
Bumped on a breaking change to :class:`DeckGateway`, :class:`Binding`, an endpoint type or
:class:`~agentdeck.bindings.gateway.GatewayFailureCode`; an added optional field never bumps it.
"""

REQUIRED_KINDS: Mapping[str, frozenset[str]] = {
    "streaming": frozenset({"text.delta"}),
    "text": frozenset({"message.completed"}),
    "hitl": frozenset({"run.interrupted"}),
    "control.cancel": frozenset(),
    "control.pause": frozenset(),
    "control.resume": frozenset(),
}
"""The canonical kinds each capability name requires in :attr:`BindingInfo.projects`
(``docs/design/protocols/rulings.md`` 26). A control capability requires none: control is an
action on ``Run``, not a projection of an event kind."""


@dataclass(frozen=True, slots=True)
class BindingInfo:
    """What a :class:`Binding` is, for ``expose()`` to validate and a health check to report."""

    name: str
    kind: Literal["protocol", "channel", "surface"]
    transport: str
    spi_version: int
    advertises: frozenset[str]
    """The capability names this binding claims (``streaming``, ``hitl``, ``control.cancel``, ...);
    ``expose()`` checks each one against a real projection or action before anything opens
    (``docs/design/protocols/rulings.md`` 26)."""

    projects: frozenset[str] = frozenset()
    """Canonical event kinds this binding maps (``run.interrupted``, ``text.delta``, ...).
    ``expose()`` computes the kinds :data:`REQUIRED_KINDS` requires from :attr:`advertises` and
    rejects any missing here, naming the binding and the gap  -  additive field, no
    :data:`PROTOCOL_SPI_VERSION` bump (``spi.md``)."""

    requires: frozenset[str] = frozenset()
    """Other bindings (by :attr:`name`) this one depends on within the same exposure; ``expose()``
    rejects a name missing from the set being exposed."""


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

    def build(self, gateway: DeckGateway) -> Endpoint:
        """Pure: no I/O, no port opened, no stdin read. Every validation this binding needs
        runs here, before anything opens, so a bad binding fails construction, not a live socket."""
        ...

    async def start(self) -> None:
        """Runs once the gateway exists, after every binding's :meth:`build`. A background task
        this spawns is owned by the Exposure's own lifecycle, never left unsupervised (ruling 32)."""
        ...

    async def stop(self) -> None:
        """Runs in the reverse of start order, including during a partial-startup rollback. Must be
        idempotent: a second call, for a binding whose own :meth:`start` never ran, is still safe."""
        ...


__all__ = [
    "PROTOCOL_SPI_VERSION",
    "Binding",
    "BindingInfo",
    "Endpoint",
    "HttpEndpoint",
    "StdioEndpoint",
]
