# Protocol SPI: versioning and packaging

Status: proposed, 2026-08-29.

## Version

```python
PROTOCOL_SPI_VERSION = 1
```

A plugin declares `spi_version = 1` in its `BindingInfo`; `expose()` refuses a binding whose `spi_version` this AgentDeck does not support, naming both versions, before anything opens. Breaking `ProtocolGateway`, `Binding`, endpoint contracts or `GatewayFailureCode` bumps the major; adding optional fields or capabilities does not.

Independent version lines, never coupled:

```text
Event schema (core/events.py CURRENT_VERSION) ≠ Protocol SPI ≠ A2A version ≠ ACP version
```

## Out-of-tree plugins

A protocol must be implementable outside this repository:

```python
from agentdeck import Deck
from agentdeck_a2a import A2A

app = Deck(...).expose(A2A.http()).asgi()
```

| may import | may not import |
|---|---|
| `agentdeck.bindings` (gateway, `Binding`, endpoint, failure types) | `agentdeck.runtime.*` |
| `agentdeck.Run`, `agentdeck.Event` | `agentdeck.adapters.*` |
| content blocks, public errors and value types | `deck._*` |

The Native binding is held to the same list. Needing a private Deck method means the gateway lacks a legitimate capability, and the gateway grows, not the import list.
