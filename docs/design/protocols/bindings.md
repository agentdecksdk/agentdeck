# Binding and endpoints

One concrete protocol over one transport it actually supports.

Status: proposed, 2026-08-29.

## No generic transport composition

Rejected:

```python
Protocol(A2A(), transport=HTTP())
```

It claims every protocol runs over every transport, which is false and leaves invalid combinations to validate forever. The protocol package decides what it supports and exposes only valid pairs:

```python
A2A.http()   A2A.grpc()
ACP.stdio()  ACP.http()
Native.http()
```

Shared HTTP and gRPC helpers live behind those factories, not in the public API.

## Shape

```python
class Binding(Protocol):
    info: BindingInfo           # name, kind ("protocol" | "channel" | "surface"), transport, spi_version, advertised capabilities
    def build(self, gateway: ProtocolGateway) -> Endpoint: ...
    async def start(self) -> None: ...   # background work the Exposure owns
    async def stop(self) -> None: ...
```

Concrete classes (`A2AHttpBinding`, `ACPStdioBinding`, ...) stay behind the factories.

## Protocols, channels and surfaces

| kind | what it is | examples | typical advertisement |
|---|---|---|---|
| protocol | machine-facing interoperability contract | A2A, ACP, MCP, AG-UI, A2UI | streaming, hitl, control |
| channel | existing communication network with its own messaging identity and API | Slack, WhatsApp, Telegram, Discord | no streaming; posts on `message.completed`; buttons from `run.interrupted` |
| surface | user-facing interface someone uses to interact with the Deck | Terminal (`agentdeck chat`), TUI, web app, desktop app | streams; prompts from `run.interrupted` |

Same contract, same gateway; the kind is data. A channel ACKs its webhook, then tails the run from a task the Exposure owns (`start()`), and keeps a durable map from its message ids to the Run address. `Terminal.stdio()` is the one surface in the v6 epic and the simplest binding: no webhook, auth, store or background task; channels are outside it; the fixture plugin under `tests/` is channel-shaped so the SPI is proven against that pattern (`rulings.md` 31 to 33).

## Endpoint types

Hosting primitives with no AgentDeck semantics.

| endpoint | carries | hosted by |
|---|---|---|
| `HttpEndpoint(path, app)` | an isolated ASGI app or router | mounted on the shared listener |
| `StdioEndpoint(run)` | a coroutine over stdin/stdout | run as a task, no port |
| `GrpcEndpoint(service)` | a gRPC servicer | gRPC server, if ever built |

stdio is why HTTP is not built into the gateway:

```text
stdin → ACP binding → ProtocolGateway → Deck → ACP binding → stdout
```

The gateway knows nothing about transport.
