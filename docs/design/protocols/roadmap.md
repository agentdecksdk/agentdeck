# Roadmap

Status: proposed, 2026-08-29.

## Sequence

| phase | deliverable | done when |
|---|---|---|
| 1 | contracts only: `ProtocolGateway` over `deck.runs`, `TargetInfo`, `Capabilities`, `GatewayFailureCode`, `ProtocolBinding`, endpoint types, `Exposure` | every contract test below that needs no real protocol passes against a fake binding |
| 2 | Native HTTP as a binding through the gateway; `agentdeck/serve.py`, `surfaces/serve/` and their goldens deleted | imports nothing private; no v1 route survives |
| 3 | AG-UI and A2UI out of core | streaming, thread mapping, cancel, tool and report projection, HITL, frontend hook, with zero special-case code inside AgentDeck |
| 4 | a structurally different protocol (ACP stdio or A2A) | works with no HTTP present |
| 5 | freeze SPI v1 | both an HTTP/chat protocol and a task/stdio protocol run cleanly |
| 6 | convenience: `deck.serve`, CLI flags, discovery/config, packaged plugins, docs | |

Phase 1 is small because `deck.runs` already has the gateway's shape (`gateway.md`).

## Target protocols

| protocol | binding | transport | notes |
|---|---|---|---|
| Native | `Native.http()` | HTTP/SSE | phase 2, the reference |
| AG-UI | `AGUI.http()` | HTTP/SSE | phase 3; CopilotKit's agent-user event stream |
| A2UI | `A2UI.http()` | HTTP/SSE | phase 3; Google's declarative agent-to-UI protocol |
| ACP | `ACP.stdio()`, later `ACP.http()` | stdio JSON-RPC | phase 4 candidate; forces the non-HTTP path |
| A2A | `A2A.http()`, later `A2A.grpc()` | HTTP JSON-RPC | phase 4 candidate; task and AgentCard projection |
| MCP server | `MCP.stdio()`, `MCP.http()` | stdio or streamable HTTP | exposes each target as an MCP tool; needs its own mini-design, `adapters/tools/mcp` is client side only |
| AgentDeck-native | `Native` itself | | a separate "own protocol" is not needed unless Native cannot carry something; decide after phase 2 |

## Rulings (decided 2026-08-29)

| question | ruling |
|---|---|
| where protocol code lives | `agentdeck/protocols/` holds the SPI; `adapters/protocols/<name>/` holds each in-tree binding; `surfaces/` keeps the CLI only |
| UI-facing protocols | both AG-UI and A2UI; Assistant UI is out of scope |
| existing serving code | `agentdeck/serve.py`, `surfaces/serve/`, their tests and goldens are deleted, not adapted; protocols start from scratch at v6 |

## Delivery

One `gh stack` rooted on `dev`. PR 1 is this design. When it is approved, the implementation plan follows as one PR per layer, bottom to top, and coding starts only once the whole stack of plan PRs is laid out.

## Contract tests before SPI v1

```text
A protocol starts a Run without touching Runtime.
A protocol tails canonical events.
A disconnected reader does not cancel execution.
A protocol reconnects from Event.seq.
A protocol recovers a Run by identity.
Cancel maps to Run.cancel(); pause/resume map to the same Run.
An interrupt is answered through Run.answer().
Two protocols expose the same Deck concurrently and see the same Run.
A Run started through protocol A is an ordinary AgentDeck Run.
Protocol metadata never appears in canonical Event payloads.
Unknown future Event kinds do not crash a plugin.
Unsupported content is rejected, not dropped.
A plugin imports no private AgentDeck module (import-linter contract).
HTTP bindings share one listener.
stdio works with no HTTP installed.
Partial startup rolls back.
Deck and binding ownership shut down in the right order.
```
