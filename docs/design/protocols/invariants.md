# Protocol invariants

## Rules

| rule | meaning |
|---|---|
| Deck owns execution | protocol code never invokes executors |
| Run is the execution identity | every external request resolves to an AgentDeck Run |
| AgentDeck events are canonical | protocols translate them, never replace them |
| Control stays on Run | cancel, pause, resume and answer are `Run` methods, nothing else |
| Protocol state is not runtime state | A2A, ACP and UI metadata stays outside the event schema and the stores |
| Protocols use public APIs only | no `_runtime`, `_start`, stores, executors, `deck._*` |
| Transport semantics belong to the protocol | no generic `Protocol(x, transport=y)` composition |
| External IDs are not core identities | the adapter maps task, session and request IDs onto `(namespace, run_id)` |
| Unsupported data is explicit | never silently drop content or events that change meaning |
| One Deck, many projections | several protocols may expose the same Runs concurrently |

The boundary:

```text
Protocol → AgentDeck public contract        always
Protocol → runtime internals                never
```

## What never enters core or runtime

```text
UI message types            A2A task models          ACP JSON-RPC models
HTTP request objects        SSE frames               WebSocket objects
gRPC services               frontend thread state    protocol connection IDs
protocol-specific persistence
```

Core stays: Invocable, Run, session correlation, Event, Control, Content, Artifacts, Context.

## Stability boundary

The SPI (`ProtocolGateway`, `Binding`, endpoint and exposure lifecycle) becomes public API. Today's ASGI routes (`agentdeck/serve.py`, `surfaces/serve/`) do not define it and may be replaced rather than contorted around.
