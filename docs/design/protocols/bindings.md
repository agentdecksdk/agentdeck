# ProtocolBinding and endpoints

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
class ProtocolBinding(Protocol):
    info: ProtocolInfo          # name, transport, spi_version
    def build(self, gateway: ProtocolGateway) -> Endpoint: ...
```

Concrete classes (`A2AHttpBinding`, `ACPStdioBinding`, ...) stay behind the factories.

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
