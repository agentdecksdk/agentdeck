# Verification

Status: Proposed canonical design

This document defines the tests and proofs required to hold the Runtime 5.1 contract.

## Lifecycle table tests

The state x action matrix in `lifecycle.md` must be represented as exhaustive table-driven tests.

Every state/action cell is asserted.

Adding a state or action without adding a ruling fails tests.

## Transition tests

Every lifecycle event maps to exactly one resulting state.

Illegal outgoing transitions from terminal states fail.

Exactly one terminal event may commit.

## Race tests

At minimum:

- pause vs cancel while running;
- duplicate cancel;
- resume vs cancel;
- answer vs cancel;
- pause vs answer;
- two concurrent answers;
- completion vs cancel;
- lost claim re-read behavior.

All outcomes must be linearizable.

## Projection tests

For generated event histories:

```text
full replay projection == incremental projection
```

Projection rebuild must produce the same state/tree as incremental application.

## Hash tests

When hash chaining is enabled:

- same event sequence -> same final hash;
- changed event -> different hash;
- reordered event -> different hash;
- projection cursor/hash must match authoritative log cursor/hash after full application.

## Execution-tree tests

Cover:

- nested workflows;
- agents calling sub-agents;
- tools;
- repeated calls to the same agent;
- parallel branches;
- multiple simultaneous asks;
- cancellation of one branch;
- paused branch;
- buffered answer branch.

## Injection tests

Cover:

- multiple injections preserve order;
- concurrent injections do not overwrite;
- pause leaves injections queued;
- cancel may leave accepted injections unapplied;
- terminal injection is refused;
- injection never answers an ask.

## Recovery tests

Crash/restart scenarios must preserve:

- suspended lifecycle states;
- buffered answer;
- accepted cancel;
- injection queue;
- exactly one terminal outcome;
- rebuildable projection.

## Contract rule

A production change that alters Runtime 5.1 behavior requires an update to the canonical design document and its corresponding contract tests.
