# DeckGateway

The stable interface from a protocol into a Deck: what an external integration may ask a Deck to do.

## Why not hand plugins the Deck

`binding.attach(deck)` makes everything public on `Deck` an implicit SPI. `binding.attach(gateway)` gives plugins a contract designed for projections, and gives AgentDeck one place to add protocol-facing capability without growing `Deck`.

## Shape

```python
class DeckGateway(Protocol):
    def targets(self) -> Sequence[TargetInfo]: ...
    capabilities: Capabilities

    async def start(self, target: str, input: Input, *, session_id: str | None = None,
                    namespace: str | None = None, key: str | None = None,
                    context: object = None) -> Run: ...
    async def get_run(self, run_id: str, *, namespace: str | None = None) -> Run: ...
    async def list_runs(self, *, namespace: str | None = None, status: RunStatus | None = None,
                        limit: int | None = None) -> Sequence[Run]: ...
```

`start`, `get_run` and `list_runs` wrap `deck.runs.start/get/list` (`agentdeck/deck.py`, class `Runs`) with the same signatures, adding what `Runs` lacks: `targets()`, `capabilities`, failure classification. Artifact bytes are out of scope (`rulings.md` 14).

Everything else is already on `Run`: `id`, `namespace`, `session_id`, `status()`, `can`, `events(from_seq=, follow=)`, `cancel()`, `pause()`, `resume()`, `pending()`, `answer()`.

## Following a run

- A follow ends at its own segment's boundary: a terminal event or a suspension.
- The binding re-tails from `last_seq + 1` after `answer()` or `resume()`.
- A re-tail started while the run is suspended blocks until the resumed segment writes, so nothing polls on a timer.
- One segment is one interaction: every protocol's HITL boundary is already a stream boundary (`rulings.md` 29).

Bindings never register `Observer`s: an observer is a deck-wide lossy telemetry tap, a protocol needs a per-run replayable stream (`rulings.md` 30). Plugins consume `Run`, never construct one, and translate only the subset of these capabilities their protocol can represent (`rulings.md` 24). The gateway grows only for an execution-model capability unreachable through it (`rulings.md` 27).

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

Enough for an A2A AgentCard skill or an MCP tool definition, which the adapter builds from it. No protocol fields, and no per-target flags: every target can stream and interrupt.

## Capabilities

Two layers:

| layer | question | where |
|---|---|---|
| deployment | `control`: control signals reach a run executing in another process, so a binding in one worker can pause or cancel it (False on `memory://`, where control still reaches runs in this process); `durable`: events survive a restart and are readable from other processes, so `from_seq` reconnect is honest (False on `memory://`) | `gateway.capabilities` |
| run | can this Run be cancelled, paused, resumed right now | `run.can` |

```python
@dataclass(frozen=True, slots=True)
class Capabilities:
    control: bool
    durable: bool
```

Only what varies per deployment. Streaming, sessions, listing, interrupts and artifacts hold for every Deck, so A2A cards and ACP or MCP `initialize` state them as constants.

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

The protocol maps each code to its own vocabulary (HTTP status, A2A error, JSON-RPC error).
