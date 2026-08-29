# ProtocolGateway

The stable interface from a protocol into a Deck: what an external integration may ask a Deck to do.

Status: proposed, 2026-08-29.

## Why not hand plugins the Deck

`binding.attach(deck)` makes everything public on `Deck` an implicit SPI. `binding.attach(gateway)` gives plugins a contract designed for projections, and gives AgentDeck one place to add protocol-facing capability without growing `Deck`.

## Shape

```python
class ProtocolGateway(Protocol):
    def targets(self) -> Sequence[TargetInfo]: ...
    capabilities: Capabilities

    async def start(self, target: str, input: Input, *, session_id: str | None = None,
                    namespace: str | None = None, key: str | None = None,
                    context: object = None) -> Run: ...
    async def get_run(self, run_id: str, *, namespace: str | None = None) -> Run: ...
    async def list_runs(self, *, namespace: str | None = None, status: RunStatus | None = None,
                        limit: int | None = None) -> Sequence[Run]: ...
    def open_artifact(self, run_id: str, artifact_id: str, *,
                      namespace: str | None = None) -> AsyncIterator[bytes]: ...
```

`start`, `get_run` and `list_runs` are `deck.runs.start`, `deck.runs.get` and `deck.runs.list` (`agentdeck/deck.py`, class `Runs`) with the same signatures. The gateway does not reimplement them; it wraps them and adds what `Runs` lacks: `targets()`, `capabilities`, `open_artifact()` (streams bytes from the `ArtifactStorePort`, scoped like `get_run`), and failure classification.

Everything else is already on `Run`: `id`, `namespace`, `session_id`, `status()`, `can`, `events(from_seq=, follow=)`, `cancel()`, `pause()`, `resume()`, `pending()`, `answer()`. Plugins consume `Run`; they never construct one.

Not introduced: `ProtocolRun`, `ProtocolEvent`, `ProtocolSession`, `ProtocolControl`. Each would duplicate an existing contract.

## Targets

```python
@dataclass(frozen=True, slots=True)
class TargetInfo:
    name: str
    kind: Literal["agent", "workflow"]
    description: str | None
    input_schema: JsonSchema | None   # None for free-text agents
```

Enough for an A2A AgentCard skill and an MCP tool definition. Never A2A AgentCard fields or ACP capability fields; those are produced by their adapter from `TargetInfo`. No per-target capability flags: every target on a deck can stream and interrupt.

## Capabilities

Two layers, never merged.

| layer | question | where |
|---|---|---|
| deployment | `control`: is a control backend configured, so pause, resume and cancel reach runs; `durable`: do events and artifacts survive restart, so `from_seq` reconnect is honest | `gateway.capabilities` |
| run | can this Run be cancelled, paused, resumed right now | `run.can` |

```python
@dataclass(frozen=True, slots=True)
class Capabilities:
    control: bool
    durable: bool
```

Only what varies per deployment. Streaming, sessions, listing, interrupts and artifacts are true for every Deck, so they are not flags; A2A cards and ACP or MCP `initialize` state them as constants.

`run.can` is informational and the `Run` methods are authoritative: a protocol may advertise cancellation and still be refused on a completed run.

## Failures

Plugins must not understand internal exception classes.

```python
class GatewayFailureCode(Enum):
    NOT_FOUND = auto()
    INVALID_INPUT = auto()
    CONFLICT = auto()
    BUSY = auto()
    UNSUPPORTED = auto()
    CANCELLED = auto()
    INTERNAL = auto()

class GatewayError(Exception):
    code: GatewayFailureCode
    message: str            # wire-safe only for NOT_FOUND, BUSY, CONFLICT, INVALID_INPUT
    cause: BaseException | None
```

One exception type; a binding writes one `except GatewayError`.

| AgentDeck error | code | HTTP precedent in `agentdeck/serve.py` |
|---|---|---|
| `NotFoundError` | `NOT_FOUND` | 404 |
| `SessionBusyError` | `BUSY` | 409 |
| `RunStateError`, `DuplicateKeyError` | `CONFLICT` | 409 |
| input coercion failure | `INVALID_INPUT` | 422 |
| unavailable control backend | `UNSUPPORTED` | none yet |
| any other `AgentdeckError` or exception | `INTERNAL` | 500, message never echoed |

Only `NOT_FOUND`, `BUSY`, `CONFLICT` and `INVALID_INPUT` messages are safe to put on the wire. The protocol maps codes to its own vocabulary (HTTP status, A2A error, JSON-RPC error).
