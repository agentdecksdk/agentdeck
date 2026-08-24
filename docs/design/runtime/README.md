# Runtime 5.1

Status: Proposed canonical design

This directory is the authoritative design source for the AgentDeck Runtime 5.1 contract.

Implementation, event schemas, tests, executors, adapters, and public documentation must conform to these documents as each area migrates.

This set is the *target*, not the runtime as built. `design/run-lifecycle.md`, `design/run-operations.md` and `design/execution-api.md` remain the record of shipped behaviour and keep winning for code that has not migrated yet. Per area, authority moves here when that area's rollout step in [migration-and-compatibility.md](./migration-and-compatibility.md) lands. See `00-project-index.md` §2 rule 8.

## Design principles

1. The durable event log is the single source of truth.
2. Runtime state is derived from durable events.
3. Every accepted lifecycle action is either applied immediately, durably pending until its delivery boundary, or explicitly resolved as a no-op or refusal.
4. No accepted action may silently disappear.
5. Concurrent lifecycle operations are linearizable: there is always a valid serial ordering that explains the resulting event log and runtime state.
6. Terminality is irreversible.
7. Derived projections may be cached and materialized, but must always be rebuildable from the event log.
8. Runtime concerns are separated by contract: lifecycle, control, input suspension, injection, execution topology, events, projections, observation, recovery, and public API.

## Decision map

| Concern | Canonical document |
|---|---|
| Run states and transitions | [lifecycle.md](./lifecycle.md) |
| Control, races, safe points | [control-and-concurrency.md](./control-and-concurrency.md) |
| Ask, answer, suspension | [input-and-suspension.md](./input-and-suspension.md) |
| Unsolicited input injection | [injection.md](./injection.md) |
| Nested execution and run tree | [execution-tree.md](./execution-tree.md) |
| Durable event model | [events.md](./events.md) |
| Derived state and materialized views | [projections.md](./projections.md) |
| Runtime views and observation | [observation.md](./observation.md) |
| Crash recovery and durability | [persistence-and-recovery.md](./persistence-and-recovery.md) |
| User-facing runtime surface | [public-api.md](./public-api.md) |
| Run identity and execution ownership | [identity-and-ownership.md](./identity-and-ownership.md) |
| Executor participation and capability | [execution-and-adapters.md](./execution-and-adapters.md) |
| Error categories and typed values | [errors-and-typing.md](./errors-and-typing.md) |
| Migration from the runtime as built | [migration-and-compatibility.md](./migration-and-compatibility.md) |
| Required tests and proofs | [verification.md](./verification.md) |

## Vocabulary

### Lifecycle states

- `running`
- `paused`
- `waiting_answer`
- `paused_waiting_answer`
- `paused_answer_ready`
- `completed`
- `failed`
- `cancelled`

### External lifecycle actions

- `pause()`
- `resume()`
- `answer(...)`
- `cancel()`

### Execution primitives

- `ctx.ask(...)`
- `ctx.safepoint()`

### Parallel runtime facility

- `inject(...)`

Injection is not a lifecycle transition. It is an ordered input facility alongside lifecycle.

## Governance

If a design change affects more than one document, the change must preserve cross-document invariants.

In particular:

- `lifecycle.md` owns the state machine.
- `control-and-concurrency.md` owns action ordering and races.
- `events.md` owns the durable representation.
- `projections.md` owns derived-state integrity.
- `public-api.md` may expose these decisions but may not redefine them.
