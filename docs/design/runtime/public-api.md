# Public API

Status: Proposed canonical design

This document defines the runtime surface exposed to developers and operators. It reflects the semantics owned by the other Runtime 5.1 documents and does not redefine them.

## Run

A `Run` is the handle for one durable execution.

Conceptual surface:

```python
class Run:
    id: str

    async def status(self) -> RunStatus: ...
    async def pause(self, reason: str | None = None) -> None: ...
    async def resume(self) -> None: ...
    async def cancel(self, reason: str | None = None) -> None: ...

    async def answer(self, ask_id: str, value: object) -> None: ...
    async def inject(self, value: object) -> None: ...

    async def tree(self) -> RunTree: ...
    def events(self, *, from_seq: int = 0, follow: bool = False): ...

    def __await__(self): ...
```

Exact naming may evolve, but the semantic categories are fixed.

## Starting

Starting creates a new Run.

Conceptually:

```python
run = await deck.runs.start(target, input)
```

or a short path:

```python
result = await deck.run(target, input)
```

Starting is not a lifecycle action on an existing Run.

## Lifecycle actions

### `pause()`

- `running`: accepted as durable control intent; effect occurs at a safe point.
- `waiting_answer`: immediate transition to `paused_waiting_answer`.
- already paused states: no-op where defined.
- terminal: no-op.
- unsupported live suspension: explicit unsupported/refused result.

### `resume()`

- `paused -> running`
- `paused_waiting_answer -> waiting_answer`
- `paused_answer_ready -> running` with stored answer
- `running`: no-op
- `waiting_answer`: refused because an answer is required
- terminal: no-op

### `answer(ask_id, value)`

- answers exactly one ask;
- does not act as generic input;
- `waiting_answer -> running`;
- `paused_waiting_answer -> paused_answer_ready`;
- second answer to an already answered ask is refused;
- terminal: no-op/refused according to ergonomic policy, but never revives execution.

### `cancel()`

- any non-terminal state can become `cancelled`;
- from `running`, cancellation may be delivered at the next safe point;
- cancellation dominates weaker pending control;
- terminal: no-op.

## Injection

```python
await run.inject(value)
```

Injection:

- is accepted only for non-terminal Runs;
- appends to an ordered durable inbox;
- does not directly change lifecycle state;
- never implicitly answers an ask.

## Ask API

Inside workflow execution:

```python
answer = await ctx.ask(question, options=...)
```

The returned value is the resolved answer.

The runtime separately exposes a durable ask identity to external answerers.

When multiple asks may be open across the execution tree, external answers must target `ask_id`.

## Safe point

Inside supported execution code:

```python
await ctx.safepoint()
```

A safe point offers a delivery boundary for pending runtime control.

It is not a lifecycle state.

## Tree view

Conceptual API:

```python
tree = await run.tree()
```

The view exposes:

- root Run;
- child Runs;
- parent/child edges;
- per-node lifecycle state;
- open asks;
- terminal outcomes;
- concurrent branches;
- optional injection summary.

The view is backed by the official projection.

## Events

```python
async for event in run.events(from_seq=0, follow=True):
    ...
```

Raw durable events remain available for audit and debugging.

Reading events does not advance execution.

## Capability

A caller may inspect capability such as:

```text
run.can.pause
run.can.resume
run.can.cancel
run.can.inject
```

if exposed.

Capability is informational.

The actual operation always performs authoritative validation against current state and executor capability.

## Errors

The API distinguishes:

- no-op: operation is harmless but unnecessary;
- refusal: operation conflicts with current lifecycle semantics;
- unsupported: executor/runtime cannot provide the capability;
- not found: unknown Run/ask;
- validation failure: invalid/unpersistable answer or injection;
- concurrency loss: normally retried/re-evaluated internally rather than exposed as stale-state behavior.

## Consistency

`status()` and `tree()` read official runtime state views/projections.

They must not depend on stale handle-local authoritative state.

## Invariants

1. Public methods do not redefine lifecycle rules.
2. External answer routing is unambiguous.
3. Injection is separate from answer.
4. Observation never advances execution.
5. Terminal Runs never revive.
