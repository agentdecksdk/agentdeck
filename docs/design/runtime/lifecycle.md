# Lifecycle

Status: Proposed canonical design

This document defines the complete AgentDeck Run lifecycle state machine.

## States

A Run is in exactly one lifecycle state:

| State | Meaning | Terminal |
|---|---|---:|
| `running` | Execution may actively advance. | no |
| `paused` | Execution is stopped by runtime control and needs no value to continue. | no |
| `waiting_answer` | Execution is suspended waiting for an answer to a specific ask. | no |
| `paused_waiting_answer` | Execution still needs an answer, and the answer gate is also paused. | no |
| `paused_answer_ready` | The required answer has arrived and is durably stored, but execution remains paused. | no |
| `completed` | Execution finished successfully. | yes |
| `failed` | Execution finished with failure. | yes |
| `cancelled` | Execution was intentionally terminated and will not continue. | yes |

The lifecycle state set is authoritative. A new state is justified only when the runtime condition has materially different legal actions or transitions from all existing states.

## Not lifecycle states

The following are not lifecycle states:

- `start`
- `pause`
- `resume`
- `answer`
- `cancel`
- `ask`
- `interrupt`
- `safepoint`
- `inject`
- `pausing`
- `resuming`
- `cancelling`
- `answering`

These are actions, execution primitives, events, delivery boundaries, or parallel runtime facilities.

## Starting a Run

Starting is not an action on an existing Run.

A new Run begins with:

```text
DOES_NOT_EXIST
    |
    | run.started
    v
RUNNING
```

There is no `created`, `pending`, or `queued` lifecycle state in this contract.

## State x action matrix

| Current state | `ctx.ask(...)` | `pause()` | `resume()` | `answer(value)` | `cancel()` |
|---|---|---|---|---|---|
| `running` | Produces an ask suspension and transitions to `waiting_answer` | Accepted as durable control intent; remains `running` until observed at a safe point, then transitions to `paused` | No-op | Refused: no ask is awaiting an answer | Accepted as durable control intent; remains `running` until observed at a safe point, then transitions to `cancelled` |
| `paused` | Not available: execution is not advancing | No-op | Transitions to `running` | Refused: use `resume()` | Transitions to `cancelled` |
| `waiting_answer` | Not available: execution is suspended | Transitions to `paused_waiting_answer` | Refused: the Run requires an answer | Transitions to `running` with the supplied answer | Transitions to `cancelled` |
| `paused_waiting_answer` | Not available | No-op | Transitions to `waiting_answer` | Stores the answer and transitions to `paused_answer_ready` | Transitions to `cancelled` |
| `paused_answer_ready` | Not available | No-op | Transitions to `running` with the stored answer | Refused: an answer is already stored | Transitions to `cancelled` |
| `completed` | Not available | No-op | No-op | No-op | No-op |
| `failed` | Not available | No-op | No-op | No-op | No-op |
| `cancelled` | Not available | No-op | No-op | No-op | No-op |

## Canonical state machine

```mermaid
stateDiagram-v2
    [*] --> running: run.started

    running --> paused: run.paused
    paused --> running: run.resumed

    running --> waiting_answer: run.interrupted
    waiting_answer --> running: run.resumed + answer
    waiting_answer --> paused_waiting_answer: run.waiting_paused

    paused_waiting_answer --> waiting_answer: run.waiting_resumed
    paused_waiting_answer --> paused_answer_ready: run.answer_buffered

    paused_answer_ready --> running: run.resumed + stored answer

    running --> completed: run.completed
    running --> failed: run.failed

    running --> cancelled: run.cancelled
    paused --> cancelled: run.cancelled
    waiting_answer --> cancelled: run.cancelled
    paused_waiting_answer --> cancelled: run.cancelled
    paused_answer_ready --> cancelled: run.cancelled

    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

## Text form

```text
START -- run.started --> RUNNING

RUNNING -- run.paused --> PAUSED
PAUSED -- run.resumed --> RUNNING

RUNNING -- run.interrupted --> WAITING_ANSWER
WAITING_ANSWER -- run.resumed + answer --> RUNNING

WAITING_ANSWER -- run.waiting_paused --> PAUSED_WAITING_ANSWER
PAUSED_WAITING_ANSWER -- run.waiting_resumed --> WAITING_ANSWER
PAUSED_WAITING_ANSWER -- run.answer_buffered --> PAUSED_ANSWER_READY
PAUSED_ANSWER_READY -- run.resumed + stored answer --> RUNNING

RUNNING -- run.completed --> COMPLETED
RUNNING -- run.failed --> FAILED

RUNNING -- run.cancelled --> CANCELLED
PAUSED -- run.cancelled --> CANCELLED
WAITING_ANSWER -- run.cancelled --> CANCELLED
PAUSED_WAITING_ANSWER -- run.cancelled --> CANCELLED
PAUSED_ANSWER_READY -- run.cancelled --> CANCELLED
```

## Terminal rules

`completed`, `failed`, and `cancelled` are absorbing.

After a terminal event:

- no lifecycle event may move the same Run again;
- `pause()`, `resume()`, `answer()`, and `cancel()` resolve as no-ops;
- a later execution creates a new Run;
- a Run may have exactly one terminal outcome.

## Lifecycle event mapping

| Event | Resulting state |
|---|---|
| `run.started` | `running` |
| `run.paused` | `paused` |
| `run.resumed` after ordinary pause | `running` |
| `run.interrupted` | `waiting_answer` |
| `run.resumed` with answer | `running` |
| `run.waiting_paused` | `paused_waiting_answer` |
| `run.waiting_resumed` | `waiting_answer` |
| `run.answer_buffered` | `paused_answer_ready` |
| `run.resumed` with stored answer | `running` |
| `run.completed` | `completed` |
| `run.failed` | `failed` |
| `run.cancelled` | `cancelled` |

## Core invariants

1. The event log determines lifecycle state.
2. No terminal state has an outgoing lifecycle transition.
3. A buffered answer is never overwritten.
4. `resume()` removes a pause; it does not synthesize a missing answer.
5. `answer()` resolves one specific ask; it does not act as generic injected input.
6. A state transition is real only once its lifecycle event is durably appended.
