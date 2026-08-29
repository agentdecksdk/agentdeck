"""The protocol SPI: what a protocol, channel or surface plugin builds against, in or out of
tree. See ``docs/design/protocols/``.
"""

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
    JsonSchema,
    TargetInfo,
)

__all__ = [
    "PROTOCOL_SPI_VERSION",
    "Binding",
    "BindingInfo",
    "Capabilities",
    "Endpoint",
    "Exposure",
    "GatewayError",
    "GatewayFailureCode",
    "HttpEndpoint",
    "JsonSchema",
    "DeckGateway",
    "StdioEndpoint",
    "TargetInfo",
]
