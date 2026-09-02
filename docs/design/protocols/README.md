# Protocol architecture

One executable Deck, exposed through many external protocols, none of which becomes part of the execution model.

Status: accepted, frozen at SPI v1 with v6.0.0 (2026-09-02). Supersedes the single-file `design.md`, kept in git history one commit back.

## Mental model

```text
 EXTERNAL SURFACES (clients, no AgentDeck code)
 ┌──────────────┐ ┌──────────────┐ ┌────────────┐ ┌──────────────┐ ┌────────────────┐
 │ Assistant UI │ │ custom React │ │  IDE       │ │ other agent  │ │ any MCP client │
 └──────┬───────┘ └──────┬───────┘ └─────┬──────┘ └──────┬───────┘ └───────┬────────┘
   react-ag-ui /      ag-ui client     ACP client      A2A client        MCP client
   react-a2a
 ═══════╪════════════════╪════════════════╪════════════════╪═════════════════╪══════ wire
        ▼                ▼                ▼                ▼                 ▼
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │  BINDINGS   adapters/bindings/<name>/        one Binding contract, kind is data   │
 │                                                                                   │
 │  kind=protocol            kind=channel             kind=surface                   │
 │  AGUI.http()              WhatsApp.http()          Terminal.stdio()  agentdeck chat│
 │  A2UI.http()              Slack.http()             TUI.stdio() WebChat.http() (later)│
 │  A2A.http()               Telegram.http()                                         │
 │  ACP.stdio()              Discord.http()                                          │
 │  MCP.stdio() MCP.http()                                                           │
 │  Native.http()   the AgentDeck protocol: versioned wire + @agentdeck/client       │
 │                                                                                   │
 │  info(kind, transport, spi_version, advertised caps) · build(gateway) → Endpoint  │
 │  start() / stop()         HttpEndpoint | StdioEndpoint                            │
 └──────────────────────────────────┬────────────────────────────────────────────────┘
                                    ▼
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │  EXPOSURE   deck.expose(...)  validate → open → gateway → start() → serve → stop()│
 │             .asgi() mounts every HttpEndpoint on one listener; .serve() standalone │
 │             one stdio binding per process; atomic startup, reverse shutdown       │
 └──────────────────────────────────┬────────────────────────────────────────────────┘
                                    ▼
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │  DeckGateway   agentdeck/bindings/          stable SPI v1                         │
 │  targets() · start() · get_run() · list_runs() · capabilities(control, durable)   │
 │  GatewayError(code) · returns Run                                                 │
 └──────────────────────────────────┬────────────────────────────────────────────────┘
                                    ▼
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │  DECK (unchanged)                                                                 │
 │  deck.runs · Run: events(from_seq, follow) cancel pause resume pending answer      │
 │              can status                                                           │
 │  targets · runs · canonical events (seq) · control · HITL · sessions · namespaces │
 │  Observers are telemetry taps here, never a binding seam                          │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

| layer | knows about | never knows about |
|---|---|---|
| external surface | one protocol's client library | AgentDeck |
| binding | its wire, the gateway, `Run`, `Event` | ports, other bindings, runtime internals |
| exposure | ports, paths, stdin, lifecycle, rollback | any protocol's semantics |
| gateway | `deck.runs`, targets, failure mapping | transports |
| deck | execution | that any binding exists |

Direction is downward: external systems adapt to AgentDeck, never the reverse.

v6.0.0 ships `Native`, `Terminal.stdio()` and `AGUI.http()`, the SPI freeze evidence (`rulings.md` 22, 37, amended: #554); `A2A.http()` and `WhatsApp.http()` follow as v6.x reference bindings. Execution semantics and the runtime stay unchanged; Deck gains the exposure entry point, `deck.serve(...)`/`deck.asgi(...)` (`expose()` for the `Exposure` object itself).

Success test: a React client, an IDE and another agent independently project the same Deck through their native protocols, every execution is still an ordinary AgentDeck Run, and the runtime contains zero protocol-specific behavior.

## Files

| file | subject |
|---|---|
| [`invariants.md`](invariants.md) | the rules every protocol implementation is held to, and what may never enter core |
| [`gateway.md`](gateway.md) | `DeckGateway`: targets, capabilities, start/get/list, failure taxonomy |
| [`projection.md`](projection.md) | how a protocol maps sessions, identities, events and input onto AgentDeck's |
| [`bindings.md`](bindings.md) | `Binding` (protocol or channel) and endpoint types: one external system over one transport it supports |
| [`exposure.md`](exposure.md) | `deck.serve`/`deck.asgi`, and `expose(...)`: validation, lifecycle, ownership, HTTP composition |
| [`spi.md`](spi.md) | SPI versioning and out-of-tree plugin packaging |
| [`agui.md`](agui.md) | the AG-UI binding: full-protocol target, Deck-level serving, identity, projection, gaps |
| [`rulings.md`](rulings.md) | every decision taken while attacking the design: chosen, rejected, why |
| [`roadmap.md`](roadmap.md) | implementation sequence, target protocols, delivery, contract tests |

## Where the design and the tree disagree

| design | tree today | consequence |
|---|---|---|
| gateway = `start/get/list` returning `Run` | `deck.runs.start/get/list` and `Run` already have that exact signature (`agentdeck/deck.py`) | the gateway is a facade over `deck.runs` plus targets, capabilities and failure mapping; Phase 1 shrinks accordingly |
| protocol code never touches `Runtime` | `surfaces/serve/app.py` takes a `Runtime` directly | ruled: deleted in Phase 2 with `agentdeck/serve.py` and the goldens; nothing is adapted |
| disconnected reader never cancels the run | `compat.py` documents that `deck.stream()` runs are deck-owned and survive reader cancellation | bindings tail `run.events()`, never a raw runtime generator, and the property holds for free |
| failure taxonomy maps to wire codes | `agentdeck/serve.py` already maps `NotFoundError` 404, `SessionBusyError` 409, `RunStateError` 409, other `AgentdeckError` 500 | precedent for `GatewayFailure`; the mapping moves behind the gateway |
| protocols live in one place | `agentdeck-v2-architecture.md` says `adapters/bindings/` (Ring 2); `engineering/architecture.md` gives `surfaces/` "protocol ingress" | ruled: SPI in `agentdeck/bindings/`, bindings in `adapters/bindings/<name>/`, `surfaces/` deleted |
| UI protocol is "Assistant UI" | v2 arch doc says `ag-ui`; the product ask says A2UI | ruled: AG-UI and A2UI ship as protocols; Assistant UI is an external surface that consumes them (or A2A) with no binding of its own |
| MCP is a tool source | `adapters/tools/mcp/` is client side only | serving the Deck as an MCP server is a new binding with its own mini-design |
