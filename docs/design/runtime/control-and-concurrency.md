# Control and Concurrency

Status: Proposed canonical design

This document defines external lifecycle actions, safe points, races, precedence, and linearization.

## External lifecycle actions

- `pause()`
- `resume()`
- `answer(...)`
- `cancel()`

`ctx.ask(...)` and `ctx.safepoint()` are execution primitives, not external control actions.

## Safe points

A safe point is not a lifecycle state.

It is an execution boundary at which a running executor allows the runtime to observe pending control.

```text
RUNNING
   |
   | safepoint
   v
read pending control
   |
   +-- none ------> RUNNING
   +-- pause -----> PAUSED
   +-- cancel ----> CANCELLED
```

A safe point may be reached at executor-defined boundaries such as:

- between workflow steps;
- between model/tool phases;
- before or after tool dispatch;
- explicit `ctx.safepoint()` calls;
- other executor-owned replay-safe boundaries.

## Delivery rules

### While `running`

`pause()` and `cancel()` are accepted before their lifecycle effect occurs.

They first become durable pending control intent.

The Run remains `running` until the executor reaches a delivery boundary and the matching lifecycle event is appended.

### While already suspended

Actions against:

- `paused`
- `waiting_answer`
- `paused_waiting_answer`
- `paused_answer_ready`

are applied through atomic lifecycle claims and do not wait for an executing task to reach a safe point.

## Linearizability

Every lifecycle action has one linearization point.

For concurrent actions there must always be a valid serial ordering that explains:

1. the durable event log;
2. the resulting Run state;
3. the action result observed by every caller.

An action that loses an atomic claim must re-read the new state and evaluate itself against that state.

It must not continue using stale state.

## No silent loss

Every accepted action must do exactly one of:

- cause an immediate durable lifecycle transition;
- become durable pending control awaiting a delivery boundary;
- resolve explicitly as a no-op;
- resolve explicitly as a refusal.

No accepted action may silently disappear.

## Pending control while running

Pending control is coalesced by strength rather than stored as an arbitrary queue.

Precedence:

```text
NONE < PAUSE < CANCEL
```

Matrix:

| Existing pending | New action | Resulting pending |
|---|---|---|
| none | `pause()` | `pause` |
| none | `cancel()` | `cancel` |
| `pause` | `pause()` | `pause` |
| `pause` | `cancel()` | `cancel` |
| `cancel` | `pause()` | `cancel` |
| `cancel` | `cancel()` | `cancel` |

Once cancellation is accepted, no weaker control action may erase it.

`resume()` is not a pending action for a `running` Run. It is a no-op.

## Duplicate actions

Duplicate actions are deterministic and idempotent where the target state already represents the requested condition.

Examples:

```text
PAUSED + pause()      -> no-op
RUNNING + resume()    -> no-op
CANCELLED + cancel()  -> no-op
```

Two concurrent `cancel()` calls may both return a successful/harmless result according to API ergonomics, but they must yield only one terminal `run.cancelled` transition.

## Important races

### `pause()` vs `answer()`

Starting from `waiting_answer`:

If pause linearizes first:

```text
WAITING_ANSWER
  -- pause() --> PAUSED_WAITING_ANSWER
  -- answer() --> PAUSED_ANSWER_READY
```

If answer linearizes first:

```text
WAITING_ANSWER
  -- answer() --> RUNNING
  -- pause() --> pending PAUSE
  -- safepoint --> PAUSED
```

Both are valid.

### pending control vs `ctx.ask()`

A Run may reach `ctx.ask(...)` with pending `pause` or `cancel` that no safe point has observed yet.

`run.interrupted` commits first. The Run becomes `waiting_answer`, which is a suspended state, so the pending control is then applied immediately through the suspended-state atomic claim rather than waiting for a further boundary:

```text
RUNNING + pending PAUSE
  -- run.interrupted --> WAITING_ANSWER
  -- pending pause applied --> PAUSED_WAITING_ANSWER
```

```text
RUNNING + pending CANCEL
  -- run.interrupted --> WAITING_ANSWER
  -- pending cancel applied --> CANCELLED
```

The ask is durably recorded either way, so a cancelled Run still shows why it stopped. `ctx.ask(...)` is not itself a safe point and no executor may treat it as one: an ask that is silently preempted leaves the workflow's own side effects half-applied with no durable record of the question.

### `cancel()` vs `answer()`

If cancel wins first:

```text
WAITING_ANSWER -> CANCELLED
```

The answer cannot revive the Run.

If answer wins first:

```text
WAITING_ANSWER -> RUNNING
```

A concurrent accepted cancel becomes pending control and must still terminate the Run at the next delivery boundary.

### `cancel()` vs `resume()`

If cancel terminalizes first, resume is a terminal no-op.

If resume wins first, the Run may become `running`, but the accepted cancel must remain effective and terminate the Run at the next delivery boundary.

### Completion/failure vs control

If `run.completed` or `run.failed` commits first, later control is a terminal no-op.

If cancellation commits first, `run.completed` and `run.failed` cannot follow.

## Answer single-consumer rule

One ask accepts one answer.

For two concurrent answers:

```text
answer(A)
answer(B)
```

exactly one may claim the ask transition.

The loser re-reads state and is refused or resolved according to the new state.

An accepted answer is never overwritten.

## Capability

Lifecycle legality and executor capability are separate dimensions.

For example:

- `running -> paused` requires an executor capable of suspending live execution at a safe point.
- `waiting_answer -> paused_waiting_answer` does not require stopping active execution because the Run is already suspended.
- `paused_answer_ready -> running` requires the executor to continue the suspended execution with the stored answer.

Capability checks exposed to callers are advisory snapshots.

The actual action must always atomically validate current state and current capability.

## Required implementation properties

- compare-and-set or equivalent atomic claims;
- no last-write-wins control slot that can overwrite accepted cancel with pause;
- durable control intent for running Runs;
- re-read after lost claims;
- exactly one terminal lifecycle transition;
- event append is the durable commit point.
