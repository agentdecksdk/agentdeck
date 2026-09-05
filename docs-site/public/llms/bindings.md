# Bindings

A binding serves a `Deck` over one protocol or surface. Add one line, and the same runs,
events, and controls are reachable through it.

## Protocols, channels, and surfaces

| kind | what it is | ships in 6.0 as |
|---|---|---|
| protocol | machine-facing interoperability contract | `Native`, `AG-UI` |
| channel | existing messaging network with its own identity | none yet |
| surface | user-facing interface AgentDeck hosts in-process | `Terminal` (`agentdeck chat`) |

Every kind builds against the same `Binding` contract and the same gateway; only the label
differs. See the
[protocol design folder](https://github.com/agentdecksdk/agentdeck/blob/main/docs/design/protocols/bindings.md)
for the full picture. An external surface such as Assistant UI or CopilotKit is a client of a
protocol binding, consuming its wire through its own library; it is not a binding of its own.

## What ships in 6.0

- `Native.http()`: AgentDeck's own HTTP/SSE protocol.
- `Terminal.stdio()`: the terminal surface, what `agentdeck chat` runs.
- `AGUI.http()`: the AG-UI protocol over HTTP/SSE.

## The front door

Serve a Deck over several bindings from one call:

```python
from agentdeck import Deck
from agentdeck.bindings import AGUI, Native, Terminal

deck = Deck.from_project()
deck.serve(Native.http("/api"), AGUI.http("/agui"), Terminal.stdio(), port=8000)
```

`deck.serve(...)` validates the set of bindings and owns their lifecycle: one process, three
bindings reaching the same runs.

Embedding inside an app you already run swaps `serve` for `deck.asgi(...)`, which returns the
ASGI application instead of blocking; `deck.expose(...)` is the lower-level call that returns the
`Exposure` object underneath both. See the [Deck reference](/reference/deck) for the full API.
