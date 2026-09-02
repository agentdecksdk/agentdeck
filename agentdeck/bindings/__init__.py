"""The protocol SPI: what a protocol, channel or surface plugin builds against, in or out of
tree. See ``docs/design/protocols/``.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from agentdeck.bindings.binding import (
    PROTOCOL_SPI_VERSION,
    Binding,
    BindingInfo,
    Endpoint,
    HttpEndpoint,
    StdioEndpoint,
)
from agentdeck.bindings.exposure import Exposure
from agentdeck.bindings.gateway import (
    Capabilities,
    DeckGateway,
    GatewayError,
    GatewayFailureCode,
    TargetInfo,
)

if TYPE_CHECKING:
    # Re-exported for the type checker only: __getattr__ below resolves these at runtime,
    # so `import agentdeck.bindings` alone still imports neither module (#606).
    from agentdeck.bindings.native import Native as Native
    from agentdeck.bindings.terminal import Terminal as Terminal

_LAZY = {"Native": "agentdeck.bindings.native", "Terminal": "agentdeck.bindings.terminal"}


def __getattr__(name: str) -> Any:
    if module_name := _LAZY.get(name):
        return getattr(import_module(module_name), name)
    raise AttributeError(f"module 'agentdeck.bindings' has no attribute {name!r}. Available: {sorted(_LAZY)}")


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "PROTOCOL_SPI_VERSION",
    "Binding",
    "BindingInfo",
    "Capabilities",
    "DeckGateway",
    "Endpoint",
    "Exposure",
    "GatewayError",
    "GatewayFailureCode",
    "HttpEndpoint",
    "Native",
    "StdioEndpoint",
    "TargetInfo",
    "Terminal",
]
