# Protocol architecture

One executable Deck, exposed through many external protocols, none of which becomes part of the execution model.

Status: proposed, 2026-08-29. Supersedes the single-file `design.md`, kept in git history one commit back.

## Mental model

```text
                    ┌─────────────────────┐
                    │        Deck         │
                    │  Agents / Workflows │
                    │         │           │
                    │        Runs         │
                    │         │           │
                    │  Events / Control   │
                    └──────────┬──────────┘
                               │
                       ProtocolGateway
                               │
                  stable protocol SPI (versioned)
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
   <UI>.http()             A2A.http()            ACP.stdio()
        │                      │                      │
        ▼                      ▼                      ▼
     Web app             Other agents                IDE
```

A protocol is a projection of a Deck. It translates between an external wire and AgentDeck's existing Run, Event and control concepts. It never executes, never owns sessions or runs, never adds an event model.

Direction is always downward: external systems adapt to AgentDeck.

## Files

| file | subject |
|---|---|
| [`invariants.md`](invariants.md) | the rules every protocol implementation is held to, and what may never enter core |
| [`gateway.md`](gateway.md) | `ProtocolGateway`: targets, capabilities, start/get/list, failure taxonomy |
| [`projection.md`](projection.md) | how a protocol maps sessions, identities, events and input onto AgentDeck's |
| [`bindings.md`](bindings.md) | `ProtocolBinding` and endpoint types: one protocol over one transport it supports |
| [`exposure.md`](exposure.md) | `deck.expose(...)`: validation, lifecycle, ownership, HTTP composition |
| [`spi.md`](spi.md) | SPI versioning and out-of-tree plugin packaging |
| [`roadmap.md`](roadmap.md) | implementation sequence, target protocols, contract tests, open rulings |

## Where the design and the tree disagree

| design | tree today | consequence |
|---|---|---|
| gateway = `start/get/list` returning `Run` | `deck.runs.start/get/list` and `Run` already have that exact signature (`agentdeck/deck.py`) | the gateway is a facade over `deck.runs` plus targets, capabilities and failure mapping; Phase 1 shrinks accordingly |
| protocol code never touches `Runtime` | `surfaces/serve/app.py` takes a `Runtime` directly | replaced by the Phase 2 native binding, not adapted |
| disconnected reader never cancels the run | `compat.py` documents that `deck.stream()` runs are deck-owned and survive reader cancellation | bindings tail `run.events()`, never a raw runtime generator, and the property holds for free |
| failure taxonomy maps to wire codes | `agentdeck/serve.py` already maps `NotFoundError` 404, `SessionBusyError` 409, `RunStateError` 409, other `AgentdeckError` 500 | precedent for `GatewayFailure`; the mapping moves behind the gateway |
| protocols live in one place | `agentdeck-v2-architecture.md` says `adapters/protocols/` (Ring 2); `engineering/architecture.md` gives `surfaces/` "protocol ingress" | needs a ruling before Phase 1 code (see `roadmap.md`) |
| UI protocol is "Assistant UI" | v2 arch doc says `ag-ui`; the product ask says A2UI | three different protocols; Phase 3 target needs a ruling |
| MCP is a tool source | `adapters/tools/mcp/` is client side only | serving the Deck as an MCP server is a new binding with its own mini-design |
