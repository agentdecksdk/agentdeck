
# AgentDeck Protocol Architecture

## Goal

AgentDeck should make one executable Deck available through many external protocols without any protocol becoming part of the execution model.

```text
                         ┌──────────── AgentDeck ─────────────┐
                         │                                   │
                         │   Agents / Workflows / Skills     │
                         │              │                    │
                         │              ▼                    │
                         │             Runs                  │
                         │              │                    │
                         │       Sessions / Events           │
                         │       Control / HITL              │
                         │                                   │
                         └──────────────┬────────────────────┘
                                        │
                              Protocol Gateway
                                        │
                  ┌─────────────────────┼──────────────────────┐
                  │                     │                      │
                  ▼                     ▼                      ▼
              Native HTTP             A2A                    ACP

                  │                     │                      │
                  ▼                     ▼                      ▼
               Web App             Other Agent             IDE
```

A protocol is a **projection of a Deck**.

It does not execute agents itself.

It does not own sessions.

It does not own Runs.

It does not create another event model.

It translates between an external protocol and AgentDeck's existing runtime concepts.

---

# 1. Hard architectural invariants

These should be treated as design rules.

| Rule                                       | Meaning                                                                      |
| ------------------------------------------ | ---------------------------------------------------------------------------- |
| Deck owns execution                        | Protocol code never invokes executors directly                               |
| Run is the execution identity              | External requests eventually resolve to AgentDeck Runs                       |
| AgentDeck events are canonical             | Protocols translate them; they do not replace them                           |
| Control stays on Run                       | Cancel/pause/resume/answer map onto Run operations                           |
| Protocol state is not runtime state        | A2A/ACP/UI-specific metadata stays outside the AgentDeck event schema        |
| Protocols use public APIs only             | No`_runtime`, `_cancel`, `_resume`, stores, executors, etc.            |
| Transport semantics belong to the protocol | No arbitrary`Protocol + Transport` combinations                            |
| External IDs are not core identities       | Protocol adapters decide how their task/session/request IDs map to AgentDeck |
| Unsupported data is explicit               | Never silently drop content/events that materially change semantics          |
| One Deck may have many projections         | Multiple protocols may expose the same underlying Runs                       |

The most important invariant is:

```text
Protocol → AgentDeck public contract

NEVER

Protocol → Runtime internals
```

---

# 2. The architecture needs three contracts

Do not build one giant `Protocol` abstraction.

There should be three distinct layers:

```text
ProtocolGateway
      │
      ▼
ProtocolBinding
      │
      ▼
Endpoint / Host
```

## ProtocolGateway

The stable interface from protocols into AgentDeck.

It represents:

> What can an external integration ask a Deck to do?

## ProtocolBinding

One concrete external protocol over one transport it actually supports.

Examples:

```text
A2A.http()
A2A.grpc()

ACP.stdio()
ACP.http()

Native.http()

AssistantUI.http()
```

## Endpoint / Host

Responsible for actual serving and process lifecycle.

Examples:

```text
HTTP routes
ASGI application
stdio loop
gRPC service
```

This means semantic execution stays completely separate from networking.

---

# 3. Do not expose generic transport composition

Avoid:

```python
Protocol(A2A(), transport=HTTP())
Protocol(ACP(), transport=GRPC())
```

That implies every protocol can work over arbitrary transports.

That is false and gives us invalid configurations to validate forever.

Instead:

```python
A2A.http()
A2A.grpc()

ACP.stdio()
ACP.http()
```

The protocol package itself decides what it supports.

Internally those bindings can reuse common HTTP/gRPC helpers.

Externally the API only exposes valid combinations.

---

# 4. Public Protocol Gateway

This is the most important contract to get right.

Conceptually:

```python
class ProtocolGateway:
    def targets(self) -> Sequence[TargetInfo]:
        ...

    async def start(
        self,
        target: str,
        input: ProtocolInput,
        *,
        session_id: str | None = None,
        namespace: str | None = None,
        key: str | None = None,
        context: object = None,
    ) -> Run:
        ...

    async def get_run(
        self,
        run_id: str,
        *,
        namespace: str | None = None,
    ) -> Run:
        ...

    async def list_runs(
        self,
        *,
        namespace: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> Sequence[Run]:
        ...
```

This is intentionally small.

Everything else already lives on `Run`.

```python
run.id

await run.status()

run.can.pause
run.can.resume
run.can.cancel

await run.cancel()
await run.pause()
await run.resume()

await run.pending()
await run.answer(value)

async for event in run.events(
    from_seq=0,
    follow=True,
):
    ...
```

We should reuse the existing public `Run` and `Event` contracts rather than create:

```text
ProtocolRun
ProtocolEvent
ProtocolSession
ProtocolControl
```

Those would just become duplicate abstractions.

The hierarchy should be:

```text
ProtocolGateway
      │
      └── returns AgentDeck Run
                         │
                         └── yields AgentDeck Event
```

---

# 5. Why have ProtocolGateway instead of handing plugins Deck?

Because giving plugins:

```python
binding.attach(deck)
```

creates a very large implicit SPI.

Anything public on `Deck` becomes something protocol plugins may start depending on.

Instead:

```python
binding.attach(gateway)
```

gives us a narrow contract specifically designed for external projections.

Internally:

```text
Deck
 │
 └── ProtocolGateway implementation
          │
          └── only calls public Deck/Run functionality
```

This also gives us somewhere to add protocol-facing capabilities later without making the Deck object itself larger.

---

# 6. Target discovery

Protocols often need to describe what they expose.

The gateway should therefore expose a small normalized target descriptor.

For example:

```python
@dataclass(frozen=True)
class TargetInfo:
    name: str
    kind: Literal[
        "agent",
        "workflow",
    ]
```

Potential later additions:

```python
description
input_modes
output_modes
metadata
capabilities
```

Do not put A2A AgentCard fields or ACP capabilities here.

Those belong to their protocols.

Instead:

```text
AgentDeck TargetInfo
        ↓
A2A adapter
        ↓
A2A AgentCard
```

---

# 7. Capabilities

Capability negotiation should happen in two layers.

## Deck-level capability

What this deployment fundamentally supports.

For example:

```text
streaming
run recovery
run listing
session-backed conversations
control backend
interrupts
artifacts
```

Conceptually:

```python
gateway.capabilities
```

## Run-level capability

What can happen to this particular Run in its current state.

That remains:

```python
run.can.cancel
run.can.pause
run.can.resume
```

The Run is authoritative.

A protocol may advertise general support for cancellation while still rejecting cancellation of a completed Run.

This distinction matters.

```text
Deployment capability:
    "I support cancellation"

Run capability:
    "this Run can currently be cancelled"
```

---

# 8. Session semantics must NOT be globally unified

This is a dangerous place to over-abstract.

AgentDeck currently has:

```python
session_id
```

which means conversation memory/context across turns.

Other protocols have concepts such as:

```text
ACP Session
A2A contextId
Assistant UI Thread
```

They are similar but not identical.

Therefore:

```text
External session concept
         ↓
Protocol adapter
         ↓
AgentDeck session_id
```

The adapter decides the mapping.

Do not define:

```text
ACP Session == AgentDeck Session
A2A Context == AgentDeck Session
UI Thread == AgentDeck Session
```

as a core invariant.

For Assistant UI it may be:

```text
threadId → session_id
```

For another protocol it may require additional protocol-local state.

---

# 9. Run identity

AgentDeck remains authoritative:

```text
(namespace, run_id)
```

Protocols may expose that identity directly when appropriate.

For example:

```text
Assistant UI generation
       → run_id

Native HTTP
       → run_id
```

But A2A may call it:

```text
taskId
```

and ACP may correlate execution differently.

Therefore:

```text
External ID
    ↕
Protocol mapping
    ↕
AgentDeck Run address
```

Never change AgentDeck's `run_id` format for a protocol.

Never put things like:

```text
a2a:task:123
acp:request:42
```

into the runtime solely because a protocol needs them.

---

# 10. Event contract

Protocols consume canonical AgentDeck Events.

For example:

```text
run.started
text.delta
message.completed

tool.call.started
tool.call.completed

agent.changed

report

run.interrupted

run.paused
run.resumed

run.completed
run.failed
run.cancelled
```

A protocol adapter performs:

```text
AgentDeck Event
       ↓
Protocol projection
```

Examples:

```text
text.delta
    ↓
Assistant UI text update

tool.call.started
    ↓
Assistant UI tool part

report
    ↓
Assistant UI status/progress

run.interrupted
    ↓
Assistant UI approval/question

run.completed
    ↓
Assistant UI generation complete
```

or:

```text
run.started
    ↓
A2A Task

report
    ↓
TaskStatusUpdate

artifact.created
    ↓
TaskArtifactUpdate

run.completed
    ↓
Task completed
```

The canonical Event schema must never gain protocol-specific types such as:

```text
a2a.task.updated
acp.session.updated
assistant_ui.message.updated
```

That would reverse the dependency.

---

# 11. Replay and reconnect

`Event.seq` should be the basis of protocol reconnection.

The gateway/Run contract already allows:

```python
run.events(
    from_seq=last_seq + 1,
    follow=True,
)
```

This means protocols can support:

```text
connection lost
      ↓
client reconnects
      ↓
recover Run
      ↓
resume from seq N
```

without changing execution.

This is a major reason the event log should remain below all protocols.

---

# 12. Input conversion

The protocol adapter converts its incoming representation into AgentDeck's supported inputs.

Examples:

```text
Protocol text
    → str / TextBlock

Protocol image
    → ImageBlock

Protocol resource
    → ResourceBlock

Structured input
    → DataBlock / workflow JSON input
```

Conversion must be explicit.

If something cannot be represented:

```text
reject
```

not:

```text
drop it and continue
```

A protocol adapter must never make the caller believe the model received information that AgentDeck discarded.

---

# 13. Error boundary

Protocol plugins should not need to understand arbitrary internal exceptions.

We should expose a small protocol-facing failure classification.

Conceptually:

```python
class GatewayFailureCode(Enum):
    NOT_FOUND = ...
    INVALID_INPUT = ...
    CONFLICT = ...
    BUSY = ...
    UNSUPPORTED = ...
    CANCELLED = ...
    INTERNAL = ...
```

The gateway converts AgentDeck errors into these categories.

The protocol then maps:

```text
Gateway BUSY
    ↓
HTTP 409

or

A2A protocol error

or

ACP JSON-RPC error
```

Internal exception messages should not automatically become wire responses.

This keeps the error taxonomy stable for external plugin authors even if internal exception classes evolve.

---

# 14. Protocol-specific state

Some protocols need state that AgentDeck itself does not.

Examples:

```text
ACP session metadata
A2A task metadata
push notification subscriptions
Assistant UI frontend metadata
protocol connection IDs
```

That state belongs to the protocol implementation.

Not:

```text
Run
Session
EventStore
```

AgentDeck should therefore initially make no generic promise to persist arbitrary protocol state.

A protocol package may own:

```text
MemoryProtocolStore
RedisProtocolStore
DatabaseProtocolStore
```

if its specification requires it.

Later, if several protocols need the same storage contract, we can extract one.

Do not preemptively add a generic protocol database to core.

---

# 15. ProtocolBinding

A binding is one concrete supported protocol + transport combination.

Conceptually:

```python
class ProtocolBinding:
    info: ProtocolInfo

    def build(
        self,
        gateway: ProtocolGateway,
    ) -> Endpoint:
        ...
```

For example:

```python
class A2AHttpBinding(ProtocolBinding):
    ...

class A2AGrpcBinding(ProtocolBinding):
    ...

class ACPStdioBinding(ProtocolBinding):
    ...

class AssistantUIHttpBinding(ProtocolBinding):
    ...
```

Factories hide those classes:

```python
A2A.http(...)
A2A.grpc(...)

ACP.stdio(...)

AssistantUI.http(...)
```

---

# 16. Endpoint types

Bindings ultimately need something the host can execute.

Internally we can have a small set of endpoint types:

```text
HttpEndpoint
StdioEndpoint
GrpcEndpoint
```

For example:

```python
HttpEndpoint(
    path="/a2a",
    app=...,
)
```

The important distinction:

These endpoint types are **hosting primitives**.

They do not contain AgentDeck semantics.

---

# 17. Exposure / Protocol Host

Deck should provide one composition object.

Recommended API:

```python
exposure = deck.expose(
    Native.http(path="/"),
    A2A.http(path="/a2a"),
)
```

Then embedded web apps can do:

```python
app = exposure.asgi()
```

while standalone use can do:

```python
await exposure.serve(
    host="0.0.0.0",
    port=8000,
)
```

Potential convenience:

```python
await deck.serve(
    Native.http(),
    A2A.http(path="/a2a"),
)
```

would simply be:

```python
await deck.expose(...).serve()
```

The deeper contract is `expose()`.

`serve()` is convenience.

---

# 18. Multiple protocols

The desired usage becomes:

```python
deck = Deck(
    agents=[assistant],
)

await deck.serve(
    Native.http(path="/"),
    A2A.http(path="/a2a"),
    AssistantUI.http(path="/assistant"),
)
```

All three are projections over:

```text
same Deck
same agents
same Runs
same sessions
same events
same controls
```

If A2A starts Run `r-123`, the Native protocol can theoretically inspect `r-123` too, assuming authorization and namespace allow it.

That is a useful architectural invariant to test.

---

# 19. HTTP composition

HTTP bindings should be able to share one listener.

Example:

```text
:8000/
      ├── /api/...          Native
      ├── /a2a/...          A2A
      └── /assistant/...    Assistant UI
```

Each binding supplies an isolated ASGI application/router.

The host mounts them.

The protocol does not start Uvicorn itself.

That lets AgentDeck work both as:

```text
standalone server
```

and:

```text
mounted inside an existing FastAPI/Starlette application
```

without changing protocol implementations.

---

# 20. stdio and other bindings

`ACP.stdio()` is different.

There is no URL path or server port.

The host therefore treats it as a runnable endpoint:

```text
stdin
 ↓
ACP binding
 ↓
ProtocolGateway
 ↓
Deck
 ↓
ACP binding
 ↓
stdout
```

This is exactly why HTTP must not be built into `ProtocolGateway`.

The gateway knows nothing about transport.

---

# 21. Lifecycle

Lifecycle should be deterministic.

```text
create Deck
    ↓
create bindings
    ↓
validate exposure
    ↓
open Deck
    ↓
build ProtocolGateway
    ↓
start bindings
    ↓
serve
    ↓
stop bindings
    ↓
close Deck if host opened it
```

Ownership rule:

> Whoever opens something closes it.

If an application has already opened the Deck, mounting a protocol must not assume ownership of it.

If `exposure.serve()` opens the Deck itself, it closes it on shutdown.

The same rule should apply to binding-owned resources.

---

# 22. Startup failure

Startup must be atomic.

If:

```text
Native HTTP ✓
A2A HTTP ✓
ACP startup ✗
```

then:

```text
stop A2A
stop Native
close resources opened by exposure
close Deck if exposure opened it
raise startup failure
```

Never leave half the protocol set running.

---

# 23. Binding conflicts

Validate before opening anything.

Examples:

```text
two HTTP bindings claim /a2a
two stdio bindings claim stdin/stdout
unsupported binding combination
invalid protocol configuration
```

These should fail during exposure validation.

Not after the server starts.

---

# 24. Protocol plugin SPI version

This contract deserves its own version.

For example:

```python
PROTOCOL_SPI_VERSION = 1
```

A plugin can declare:

```python
spi_version = 1
```

Breaking changes to:

```text
ProtocolGateway
ProtocolBinding
Endpoint contracts
Gateway failures
```

require an SPI major bump.

Adding optional fields/capabilities should not.

The AgentDeck Event schema already has independent versioning.

Keep these separate:

```text
AgentDeck Event schema
        ≠
Protocol Plugin SPI
        ≠
A2A version
        ≠
ACP version
```

---

# 25. Plugin packaging

A protocol should be implementable outside AgentDeck.

For example:

```text
agentdeck-a2a
agentdeck-acp
agentdeck-assistant-ui
```

Usage:

```python
from agentdeck import Deck
from agentdeck_assistant_ui import AssistantUI

deck = Deck(...)

app = deck.expose(
    AssistantUI.http(),
).asgi()
```

Nothing in that package should import:

```python
agentdeck.runtime.*
agentdeck.adapters.executors.*
agentdeck.adapters.stores.*
deck._*
```

Allowed dependencies should look roughly like:

```text
agentdeck.protocols
agentdeck.Run
agentdeck.Event
agentdeck content models
public errors/value types
```

---

# 26. Assistant UI as the architecture proof

Assistant UI is a very good test case for the SPI.

The final UX should eventually be:

## Python

```python
from agentdeck import Agent, Deck
from agentdeck_assistant_ui import AssistantUI


assistant = Agent(
    name="assistant",
    instructions="Help the user.",
)

deck = Deck(agents=[assistant])

await deck.serve(
    AssistantUI.http(),
)
```

## React

```tsx
import { useAgentDeckRuntime } from "@agentdeck/assistant-ui";

const runtime = useAgentDeckRuntime({
  url: "/assistant",
  agent: "assistant",
});
```

Then:

```tsx
<AssistantRuntimeProvider runtime={runtime}>
  <Thread />
</AssistantRuntimeProvider>
```

No custom backend route.

No custom `ChatModelAdapter`.

No manually parsing SSE.

No AgentDeck HTTP implementation knowledge.

No runtime internals.

That is what "plug and play" should mean.

---

# 27. How Assistant UI would implement this

Internally:

```text
Assistant UI composer
       ↓
AssistantUI HTTP protocol
       ↓
ProtocolGateway.start()
       ↓
Run
       ↓
Run.events(follow=True)
       ↓
AssistantUI protocol projection
       ↓
useAgentDeckRuntime
       ↓
Assistant UI
```

Cancellation:

```text
Assistant UI Stop
       ↓
AssistantUI protocol
       ↓
gateway.get_run()
       ↓
run.cancel()
```

Tools:

```text
tool.call.started
       ↓
AssistantUI protocol projection
       ↓
Assistant UI tool part
```

Reporter:

```text
report
       ↓
Assistant UI status/progress part
```

HITL:

```text
run.interrupted
       ↓
Assistant UI interaction
       ↓
user answer
       ↓
run.answer(value)
```

This requires zero special-case code inside AgentDeck.

That is the acceptance criterion.

---

# 28. What must NOT enter core

Do not put these into AgentDeck's runtime/core:

```text
Assistant UI message types
A2A task models
ACP JSON-RPC models
HTTP request objects
SSE frames
WebSocket objects
gRPC services
frontend thread state
protocol connection IDs
protocol-specific persistence
```

Core should remain:

```text
Invocable
Run
Session correlation
Event
Control
Content
Artifacts
Context
```

---

# 29. Stability boundary

The protocol SPI becomes a real public API.

The current convenience HTTP/ASGI implementation does not need to define that contract.

That means we should be willing to replace the existing serving implementation rather than contort the new architecture around it.

The new stability promise should be:

```text
ProtocolGateway
ProtocolBinding
Endpoint / Exposure lifecycle
```

not:

```text
the exact implementation of today's ASGI routes
```

---

# 30. Implementation sequence

The order matters.

**Phase 1: Write the contracts only.** Define `ProtocolGateway`, target/capability models, gateway failure classification, `ProtocolBinding`, endpoint types, and exposure lifecycle. No A2A or ACP yet.

**Phase 2: Native HTTP as reference implementation.** Build a new native binding entirely through `ProtocolGateway`. If it needs a private Deck method, the gateway is missing a legitimate capability.

**Phase 3: Assistant UI proof.** Implement `AssistantUI.http()` outside core. This tests streaming, session mapping, cancellation, tool/report projection, and frontend integration.

**Phase 4: structurally different protocol.** Implement ACP stdio or A2A. This is important because Assistant UI + Native HTTP alone would bias the SPI toward HTTP/chat semantics.

**Phase 5: freeze SPI v1.** Only after both an HTTP/UI-style protocol and a task/session or stdio-style protocol work cleanly should we declare the plugin contract stable.

**Phase 6: convenience APIs and CLI.** Add `deck.serve(...)`, protocol discovery/config, CLI flags, packaged plugins, docs and examples.

---

# 31. Required contract tests

Before calling the SPI stable, these cases should pass:

```text
A protocol starts a Run without touching Runtime.

A protocol tails canonical events.

A disconnected reader does not accidentally cancel execution.

A protocol can reconnect using event seq.

A protocol can recover a Run by identity.

Cancel maps to Run.cancel().

Pause/resume map to the same Run.

An interrupt can be answered.

Two protocols can expose the same Deck concurrently.

A Run started through protocol A is still an ordinary AgentDeck Run.

Protocol-specific metadata never appears in canonical Event payloads.

Unknown future Event kinds do not crash old plugins.

Unsupported content is rejected rather than silently lost.

A plugin can live in a separate repository.

A plugin imports no AgentDeck private module.

HTTP bindings can share a server.

stdio works without HTTP existing at all.

Partial startup is rolled back.

Deck ownership and binding ownership shut down correctly.
```

---

# 32. The core mental model

This is the model I would put into the architecture docs:

```text
                    ┌─────────────────────┐
                    │        Deck         │
                    │                     │
                    │  Agents / Workflows │
                    │         │           │
                    │        Runs         │
                    │         │           │
                    │ Events / Control    │
                    └──────────┬──────────┘
                               │
                       ProtocolGateway
                               │
              stable AgentDeck protocol SPI
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
 AssistantUI.http()        A2A.http()            ACP.stdio()
        │                      │                      │
        ▼                      ▼                      ▼
 Assistant UI             Agent systems               IDE
```

The important direction is always downward.

External systems adapt to AgentDeck.

AgentDeck does not adapt its runtime model to whichever protocol happens to be popular.

---

# Final contract

The baseline I would freeze conceptually is:

```python
ProtocolGateway
    ├── targets()
    ├── capabilities
    ├── start(...) -> Run
    ├── get_run(...) -> Run
    └── list_runs(...) -> Sequence[Run]

Run
    ├── id / namespace / session_id
    ├── status()
    ├── can
    ├── events(...)
    ├── cancel()
    ├── pause()
    ├── resume()
    ├── pending()
    └── answer(...)

ProtocolBinding
    └── build(gateway) -> Endpoint

Endpoint
    ├── HTTP
    ├── stdio
    └── other hostable binding

Exposure
    ├── validates bindings
    ├── manages lifecycle
    ├── asgi()
    └── serve()
```

Everything above that is a protocol implementation.

Everything below it is AgentDeck.

That is the boundary we should design and test before implementing protocol support.
