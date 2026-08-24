# Input and Suspension

Status: Proposed canonical design

This document defines `ctx.ask(...)`, ask identity, answer routing, multiple simultaneous asks, answer buffering, and suspension semantics.

## Ask is an execution primitive

`ctx.ask(...)` is called by running workflow code.

It is not an external control action.

Conceptually:

```text
RUNNING
   |
   | ctx.ask(...)
   v
run.interrupted
   |
   v
WAITING_ANSWER
```

The lifecycle transition becomes real when `run.interrupted` is durably appended.

## Ask identity

Every ask has a durable unique identity.

A caller must never answer "the current question" implicitly when more than one ask may exist in the execution tree.

A public ask representation should include at minimum:

```text
ask_id
run_id
origin node / branch identity
question or payload
options / expected answer contract when defined
status
```

Additional presentation metadata may be included, but routing must rely on durable identity.

## Multiple simultaneous asks

Parallel branches may suspend independently.

Example:

```text
ROOT
├── branch A -> ASK ask-101
└── branch B -> ASK ask-202
```

Both asks may be open simultaneously.

Answers must target the intended ask:

```text
answer(ask_id="ask-101", value=...)
answer(ask_id="ask-202", value=...)
```

An API that only accepts `answer(value)` is sufficient only when the target Run is guaranteed to have exactly one answerable ask.

The runtime design must not rely on that guarantee for nested or parallel execution.

## Answer semantics

`answer(...)` is a continuation action.

It is not generic injected input.

### `waiting_answer`

```text
WAITING_ANSWER
   |
   | answer(value)
   v
RUNNING
```

The answer is durably associated with the ask and continuation.

### `paused_waiting_answer`

```text
PAUSED_WAITING_ANSWER
   |
   | answer(value)
   v
PAUSED_ANSWER_READY
```

The answer is durably stored, but execution remains paused.

### `paused_answer_ready`

A second answer is refused.

The accepted answer is immutable.

```text
PAUSED_ANSWER_READY + answer(...)
    -> refused
```

## Resume semantics around asks

```text
PAUSED_WAITING_ANSWER
    -- resume() --> WAITING_ANSWER
```

Removing the pause does not invent an answer.

```text
PAUSED_ANSWER_READY
    -- resume() --> RUNNING
```

The stored answer is supplied to the continuation.

## Validation

If an ask declares answer options or a closed answer type, validation happens before the continuation claim commits.

An invalid answer must not move the lifecycle state.

Free-form asks may accept any persistable answer unless an application-defined validator is part of the ask contract.

## Durability

An accepted answer must be durably reproducible.

The runtime must not hand an executor an answer that cannot be represented in the durable event model.

If a value cannot be persisted under the event schema, it is refused before the lifecycle claim commits.

## Parallel suspension

Each branch may be suspended independently.

The top-level execution tree may therefore contain:

- running branches;
- waiting branches;
- paused branches;
- completed branches;
- cancelled branches;

at the same time.

The top-level Run view must expose the open asks and their origin branches.

## Ask lifecycle

An ask itself may have a derived view state such as:

- `open`
- `answered`
- `cancelled`
- `superseded` if ever explicitly introduced

These are ask-view states, not Run lifecycle states.

## Invariants

1. Every ask has a durable identity.
2. One ask accepts at most one answer.
3. An answer cannot accidentally resolve a different ask.
4. Generic injection never implicitly answers an ask.
5. Buffered answers survive pause and recovery.
6. Answer routing remains correct under parallel execution.
