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
```

`start`, `get_run` and `list_runs` are `deck.runs.start`, `deck.runs.get` and `deck.runs.list` (`agentdeck/deck.py`, class `Runs`) with the same signatures. The gateway does not reimplement them; it wraps them and adds the three things `Runs` lacks: `targets()`, `capabilities`, and failure classification.

Everything else is already on `Run`: `id`, `namespace`, `session_id`, `status()`, `can`, `events(from_seq=, follow=)`, `cancel()`, `pause()`, `resume()`, `pending()`, `answer()`. Plugins consume `Run`; they never construct one.

Not introduced: `ProtocolRun`, `ProtocolEvent`, `ProtocolSession`, `ProtocolControl`. Each would duplicate an existing contract.

## Targets

```python
@dataclass(frozen=True, slots=True)
class TargetInfo:
    name: str
    kind: Literal["agent", "workflow"]
```

Later, if earned: `description`, `input_modes`, `output_modes`, `metadata`. Never A2A AgentCard fields or ACP capability fields; those are produced by their adapter from `TargetInfo`.

## Capabilities

Two layers, never merged.

| layer | question | where |
|---|---|---|
| deployment | does this Deck support streaming, run recovery, run listing, sessions, control backend, interrupts, artifacts | `gateway.capabilities` |
| run | can this Run be cancelled, paused, resumed right now | `run.can` |

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
```

| AgentDeck error | code | HTTP precedent in `agentdeck/serve.py` |
|---|---|---|
| `NotFoundError` | `NOT_FOUND` | 404 |
| `SessionBusyError` | `BUSY` | 409 |
| `RunStateError`, `DuplicateKeyError` | `CONFLICT` | 409 |
| input coercion failure | `INVALID_INPUT` | 422 |
| unavailable control backend | `UNSUPPORTED` | none yet |
| any other `AgentdeckError` or exception | `INTERNAL` | 500, message never echoed |

Only `NOT_FOUND`, `BUSY`, `CONFLICT` and `INVALID_INPUT` messages are safe to put on the wire. The protocol maps codes to its own vocabulary (HTTP status, A2A error, JSON-RPC error).
