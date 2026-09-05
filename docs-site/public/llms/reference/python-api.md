# Python API

Core types and public classes exported from `agentdeck`, plus the protocol SPI in
`agentdeck.bindings`.

## Exports

- `Deck`: Unified composition root.
- `Agent`: Declarative agent specification.
- `tool`: Decorator declaring a leaf capability.
- `workflow`: Decorator declaring orchestration as an async function.
- `ToolCtx`: The context a tool receives.
- `WorkflowCtx`: `ToolCtx` plus orchestration, for a `@workflow` body.
- `AgentInstance`: One agent as something runnable: what `ctx.agent` is and `ctx.agents.create()` mints.
- `Run`: A deck-bound handle on one run: `status()`, `pause()`, `resume()`, `cancel()`, `pending()`, `answer()`, `events()`.
- `TurnResult`: One agent turn's outcome, what `deck.run()` returns: `output`, `usage`, `run_id`, `session_id`.
- `Observer`: One consumer of a deck's event stream, for `Deck(observers=[...])`.
- `Event`: One canonical event, what `run.events()` yields and an `Observer` receives.
- `RunStatus`: A run's lifecycle state, what `deck.runs.list(status=...)` filters on.
- `views`: Declarative predicates over the event stream, composed with `|`, `&` and `~`.
- `__version__`: The installed version.

### Content blocks

What `deck.run()` takes instead of a plain string, and what a run reports back.

- `ContentBlock`: The union of the block types below.
- `TextBlock`: Prose.
- `ImageBlock`: An image inline, base64.
- `AudioBlock`: Audio inline, base64.
- `ResourceBlock`: Bytes held elsewhere, referenced rather than carried.
- `DataBlock`: JSON as content.

### Bindings

From `agentdeck.bindings`, not the root package. `Native`, `Terminal` and `AGUI` resolve lazily, so
importing the SPI pulls in neither an HTTP stack nor the AG-UI models. The guide is
[Bindings](/bindings).

- `Native`: AgentDeck's own HTTP/SSE protocol, via `Native.http()`.
- `Terminal`: one session per process, via `Terminal.stdio()`.
- `AGUI`: the AG-UI protocol, via `AGUI.http()`; needs the `agui` extra.
- `Binding`: the contract a binding implements: `info`, `build()`, `start()`, `stop()`.
- `BindingInfo`: what a binding declares about itself, including what it `advertises`.
- `Endpoint`: `HttpEndpoint | StdioEndpoint`, what `build()` returns.
- `HttpEndpoint`: an ASGI app mounted at a path.
- `StdioEndpoint`: a coroutine over stdin and stdout, with no port opened.
- `Exposure`: hosts a validated set of bindings, what `deck.expose()` returns.
- `DeckGateway`: the whole surface a binding may touch on a deck.
- `TargetInfo`: one agent or workflow a protocol may start a run against.
- `Capabilities`: what varies by deployment rather than by run.
- `GatewayError`: a gateway call that failed, carrying a `GatewayFailureCode`.
- `GatewayFailureCode`: why it failed, for a binding to map onto its own protocol.
- `PROTOCOL_SPI_VERSION`: the SPI version a binding declares. Frozen at 1.
