# Binding and endpoints

One concrete protocol over one transport it actually supports.

## No generic transport composition

Rejected:

```python
Protocol(A2A(), transport=HTTP())
```

It claims every protocol runs over every transport, which is false and leaves invalid combinations to validate forever. Each protocol package exposes only the pairs it supports:

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
| surface | user-facing interface AgentDeck hosts in-process | Terminal (`agentdeck chat`), TUI | streams; prompts from `run.interrupted` |

Same contract, same gateway; the kind is data. A channel ACKs its webhook, then tails the run from an Exposure-owned task (`start()`), keeping a durable map from its message ids to the Run address. The fixture plugin under `tests/` is channel-shaped, so the SPI is proven against that harder pattern (`rulings.md` 31 to 33).

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

## External surfaces are clients, not bindings

An external surface (Assistant UI, a custom React app, an IDE, another agent) consumes a binding's wire through its own client library; the diagram is in [`README.md`](README.md). Assistant UI ships AG-UI and A2A runtimes, so the v6.0 web UI is one of those runtimes pointed at a binding's path, with no AgentDeck code (`rulings.md` 38).

## The reference trio

Same intent down each kind; the bold rows are identical code against public `Run`.

| step | A2A (protocol) | WhatsApp (channel) | Terminal (surface) |
|---|---|---|---|
| arrives as | `message/send {contextId, message}` | webhook `{from, text}`, ACK 200 at once | a typed line |
| identity | `contextId` to `session_id`; `taskId` to `key` | phone to `session_id`; message id in the binding's map | one session per process |
| **`gateway.start(target, text, session_id=)`** | same | same | same |
| busy session | A2A "task running" error | "still working on your last message" | printed notice |
| **`run.events(follow=True)`**, one segment per interaction | Exposure-owned task | Exposure-owned task | inline in the stdio loop |
| `text.delta` | streamed parts | skipped; posts on `message.completed` | printed live |
| `run.interrupted` | `input-required` with the question | reply buttons | numbered prompt |
| **`run.answer(value)`**, then re-tail from `last_seq + 1` | next `message/send` on the task | button webhook, run found in the map | typed choice |
| stop | `tasks/cancel` | "stop" keyword | Ctrl-C |
| **`run.cancel()`** | same | same | same |
| binding-owned state | task metadata, push subscriptions | phone-to-run map, access token | none |
| enters the runtime | nothing | nothing | nothing |
