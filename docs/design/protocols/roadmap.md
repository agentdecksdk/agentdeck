# Roadmap

## Sequence

| phase | deliverable | done when |
|---|---|---|
| 1 | contracts only: `DeckGateway` over `deck.runs`, `TargetInfo`, `Capabilities`, `GatewayFailureCode`, `Binding` with `start`/`stop`, endpoint types, `Exposure`, channel-shaped fixture plugin | every contract test below that needs no real protocol passes against a fake binding |
| 2 | Native HTTP and `Terminal.stdio()` (the first surface) as bindings, with a versioned wire spec and `@agentdeck/client` through the gateway; `agentdeck/serve.py`, all of `surfaces/`, the `agentdeck-serve` script and the goldens deleted; `agentdeck chat` runs `Terminal.stdio()` | imports nothing private; no v1 route survives; `engineering/architecture.md` ownership table updated |
| 3 | `A2A.http()` (protocol) | tasks, `contextId` session, `taskId` key, `input-required` HITL, `tasks/resubscribe` from `seq`, AgentCard from `TargetInfo` |
| 4 | `WhatsApp.http()` (channel) | webhook ACK then Exposure-owned tail, `message.completed` posting, reply buttons for HITL, durable phone-to-run map |
| 5 | freeze SPI v1 | the trio runs on one Deck: a run started over A2A is visible from the Terminal, WhatsApp answers an interrupt, every contract test passes |
| 6 | AG-UI, A2UI, ACP, MCP server as 6.x minors; convenience: `deck.serve` sugar, `.agentdeck/bindings` config, CLI flags, extras, docs | v6.x |

Phase 1 is small because `deck.runs` already has the gateway's shape (`gateway.md`).

## Target protocols

| protocol | binding | transport | notes |
|---|---|---|---|
| Native | `Native.http()` | HTTP/SSE | phase 2, the reference; the AgentDeck protocol, versioned spec plus JS client (`rulings.md` 18) |
| AG-UI | `AGUI.http()` | HTTP/SSE | 6.x; CopilotKit's agent-user event stream, planned in [`agui.md`](agui.md) |
| A2UI | `A2UI.http()` | HTTP/SSE | 6.x; Google's declarative agent-to-UI protocol |
| ACP | `ACP.stdio()`, later `ACP.http()` | stdio JSON-RPC | 6.x |
| A2A | `A2A.http()`, later `A2A.grpc()` | HTTP JSON-RPC | phase 3, the reference protocol |
| WhatsApp | `WhatsApp.http()` | HTTP webhook + Cloud API | phase 4, the reference channel |
| MCP server | `MCP.stdio()`, `MCP.http()` | stdio or streamable HTTP | one tool per target, progress notifications, elicitation for HITL, runs as resources (`rulings.md` 17) |
| Terminal | `Terminal.stdio()` | stdio | surface; `agentdeck chat`; phase 2 (`rulings.md` 35) |

Every decision behind this sequence is in [`rulings.md`](rulings.md); the ones that shape it most are 34 (`surfaces/` deleted), 36 (package names) and 37 (the v6.0 trio). v6.0.0 is the trio on one Deck:

```python
await deck.expose(A2A.http(path="/a2a"), WhatsApp.http(path="/whatsapp"), Terminal.stdio()).serve()
```

## Delivery

One `gh stack` rooted on `dev`; PR 1 is this design (#539, closes #543). Epic #129 carries one issue per story (#544 to #554) and is the plan of record. Each story opens its stacked draft PR when it starts, with the design in the PR body, and closes its issue. The Artifacts epic is independent and not on this stack.

## Contract tests before SPI v1

Proven in `tests/bindings/test_contract.py` against the out-of-tree fixture, except the last four
(shared listener, stdio, rollback, shutdown order), which are Exposure's own and are proven in
`tests/test_bindings_exposure.py`. Control mapping is `Run`'s contract: the gateway hands back
real `Run`s, which `tests/test_bindings_gateway.py` proves.

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
