# Protocol SPI: versioning and packaging

## Version

```python
PROTOCOL_SPI_VERSION = 1
```

A plugin declares `spi_version = 1` in its `BindingInfo`; `expose()` refuses a binding whose `spi_version` this AgentDeck does not support, naming both versions, before anything opens. Breaking `DeckGateway`, `Binding`, endpoint contracts or `GatewayFailureCode` bumps the major; adding optional fields or capabilities does not.

Independent version lines, never coupled:

```text
Event schema (core/events.py CURRENT_VERSION) ≠ Protocol SPI ≠ A2A version ≠ ACP version
```

## Frozen at v1

`PROTOCOL_SPI_VERSION = 1` is frozen with v6.0.0 (`rulings.md` 22, 37, amended: #554). v1 covers
`Binding` and `BindingInfo`, `DeckGateway` and its methods (`targets`, `start`, `get_run`,
`list_runs`, `capabilities`), `GatewayError`/`GatewayFailureCode`, `HttpEndpoint`/`StdioEndpoint`,
and `Exposure` (`deck.serve`/`deck.asgi`/`deck.expose`). The freeze evidence is the contract suite
(`tests/bindings/test_contract.py` against the out-of-tree fixture plugin) plus
`tests/bindings/test_three_bindings_one_deck.py`, proving all three shipped kinds (protocol:
Native and AG-UI, surface: Terminal) and, via the fixture plugin, the channel kind (rulings 19,
32) on one Deck. A v2 would be a breaking change to any of the surface above: a removed or
reshaped method, a new required field, a changed failure code's meaning. A new capability name in
`BindingInfo.advertises`, a new binding, or a new optional field is not a version bump.

## Out-of-tree plugins

A protocol must be implementable outside this repository:

```python
from agentdeck import Deck
from agentdeck_a2a import A2A

app = Deck(...).asgi(A2A.http())
```

| may use | may not use |
|---|---|
| `agentdeck.bindings` (gateway, `Binding`, endpoint, failure types) | `agentdeck.runtime.*` |
| `agentdeck.Run`, `agentdeck.Event` | `agentdeck.adapters.*` |
| content blocks from `agentdeck` (`TextBlock`, `ImageBlock`, ...) | `agentdeck.core.*` |
| the error taxonomy from `agentdeck.errors` | `Deck._*` |

The Native binding is held to the same list. Needing a private Deck method means the gateway lacks a legitimate capability, and the gateway grows, not this list.
