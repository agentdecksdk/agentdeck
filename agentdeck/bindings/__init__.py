"""The protocol SPI: what a protocol, channel or surface plugin builds against, in or out of
tree. See ``docs/design/protocols/``.

The content blocks are re-exported here, not reached through ``agentdeck.core``: a plugin builds
against this package alone.
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
    TargetInfo,
)
from agentdeck.core.content import (
    AudioBlock,
    ContentBlock,
    DataBlock,
    ImageBlock,
    ResourceBlock,
    TextBlock,
)

__all__ = [
    "PROTOCOL_SPI_VERSION",
    "AudioBlock",
    "Binding",
    "BindingInfo",
    "Capabilities",
    "ContentBlock",
    "DataBlock",
    "DeckGateway",
    "Endpoint",
    "Exposure",
    "GatewayError",
    "GatewayFailureCode",
    "HttpEndpoint",
    "ImageBlock",
    "ResourceBlock",
    "StdioEndpoint",
    "TargetInfo",
    "TextBlock",
]
