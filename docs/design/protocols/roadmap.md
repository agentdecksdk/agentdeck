# Roadmap

Status: proposed, 2026-08-29.

## Sequence

| phase | deliverable | done when |
|---|---|---|
| 1 | contracts only: `ProtocolGateway` over `deck.runs`, `TargetInfo`, `Capabilities`, `GatewayFailureCode`, `ProtocolBinding`, endpoint types, `Exposure` | every contract test below that needs no real protocol passes against a fake binding |
| 2 | Native HTTP rebuilt as a binding through the gateway | replaces `agentdeck/serve.py` and `surfaces/serve/`; imports nothing private |
| 3 | UI-facing protocol out of core | streaming, thread mapping, cancel, tool and report projection, HITL, frontend hook, with zero special-case code inside AgentDeck |
| 4 | a structurally different protocol (ACP stdio or A2A) | works with no HTTP present |
| 5 | freeze SPI v1 | both an HTTP/chat protocol and a task/stdio protocol run cleanly |
| 6 | convenience: `deck.serve`, CLI flags, discovery/config, packaged plugins, docs | |

Phase 1 is small because `deck.runs` already has the gateway's shape (`gateway.md`).

## Target protocols

| protocol | binding | transport | notes |
|---|---|---|---|
| Native | `Native.http()` | HTTP/SSE | phase 2, the reference |
| UI | `<UI>.http()` | HTTP/SSE | phase 3; identity of the protocol is an open ruling |
| ACP | `ACP.stdio()`, later `ACP.http()` | stdio JSON-RPC | phase 4 candidate; forces the non-HTTP path |
| A2A | `A2A.http()`, later `A2A.grpc()` | HTTP JSON-RPC | phase 4 candidate; task and AgentCard projection |
| MCP server | `MCP.stdio()`, `MCP.http()` | stdio or streamable HTTP | exposes each target as an MCP tool; needs its own mini-design, `adapters/tools/mcp` is client side only |
| AgentDeck-native | `Native` itself | | a separate "own protocol" is not needed unless Native cannot carry something; decide after phase 2 |

## Open rulings

| question | options | recommendation |
|---|---|---|
| where protocol code lives | `adapters/protocols/<name>/` (v2 arch doc, independently deletable) vs `surfaces/<name>/` (engineering/architecture.md) | `agentdeck/protocols/` for the SPI, `adapters/protocols/<name>/` for in-tree bindings; surfaces keeps CLI |
| phase 3 UI protocol | Assistant UI (design.md), AG-UI (v2 arch doc), A2UI (product ask) | pick one; the others become out-of-tree plugins later |
| fate of `agentdeck/serve.py` v1 routes | keep behind Native binding with golden tests, or drop at v6 | drop at v6 (major), keep goldens only for the new native wire |

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
