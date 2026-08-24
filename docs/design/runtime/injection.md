# Injection

Status: Proposed canonical design

This document defines unsolicited application input delivered to an existing non-terminal Run.

## Definition

Injection adds new information to a Run without resolving a specific ask and without directly changing lifecycle state.

Conceptual API:

```python
await run.inject(value)
```

Injection is not:

- `answer(...)`;
- `pause()`;
- `resume()`;
- `cancel()`;
- a lifecycle state transition.

## Lifecycle relationship

Injection leaves lifecycle state unchanged.

| Current state | `inject(value)` |
|---|---|
| `running` | Append to inbox; state unchanged |
| `paused` | Append to inbox; state unchanged |
| `waiting_answer` | Append to inbox; state unchanged |
| `paused_waiting_answer` | Append to inbox; state unchanged |
| `paused_answer_ready` | Append to inbox; state unchanged |
| `completed` | Refused |
| `failed` | Refused |
| `cancelled` | Refused |

In particular:

```text
WAITING_ANSWER + inject("yes")
```

does not mean:

```text
answer("yes")
```

## Ordered inbox

Injection is zero-to-many and must be modeled as an ordered durable inbox.

```text
Run
└── injection inbox
    ├── I1
    ├── I2
    └── I3
```

Multiple injections must not overwrite or coalesce each other.

If:

```text
inject(A)
inject(B)
inject(C)
```

all are accepted, all three remain represented in durable order.

## Ordering

Concurrent injections may linearize in either order:

```text
A -> B
```

or:

```text
B -> A
```

but every accepted injection receives exactly one durable position in the Run's injection stream.

No accepted injection may disappear.

## Consumption

The executor consumes injections only at defined input boundaries.

The contract must distinguish at least:

- injection accepted;
- injection available;
- injection consumed/applied.

Whether consumption is exactly-once at the application effect level depends on executor replay semantics.

The runtime contract guarantees that each accepted injection is durably represented exactly once and that consumption state is derivable.

## Delivery priority

At an execution boundary, control dominates unsolicited input.

Recommended priority:

```text
CANCEL
  >
PAUSE
  >
INJECTION DELIVERY
  >
CONTINUE
```

Therefore:

```text
inject(A)
cancel()
```

may leave A durably accepted but unapplied if cancellation wins before the next input boundary.

Similarly:

```text
inject(A)
pause()
```

leaves A queued while the Run becomes paused.

After resume, the executor may consume A.

## Events

The durable model distinguishes receipt from application:

| Fact | Kind |
|---|---|
| Injection accepted | `input.appended` |
| Injection consumed by the executor | `input.consumed` |

`input.appended` is already in the schema and has no producer today. Injection is its producer, so injection adds one kind, not two. See `events.md`.

## Terminality

Injection after a terminal event is refused.

A Run is never revived by injected input.

## Invariants

1. Injection never directly changes Run lifecycle state.
2. Injection never implicitly resolves an ask.
3. Multiple injections preserve all accepted values in durable order.
4. Accepted injections survive pause and process restart.
5. Terminal Runs reject new injections.
6. Cancellation may prevent an accepted injection from ever being consumed.
