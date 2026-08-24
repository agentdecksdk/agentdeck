# Persistence and Recovery

Status: Proposed canonical design

This document defines durability, crash recovery, replay, projection recovery, suspended-state recovery, and terminal guarantees.

## Durable authority

The event log is the durable authority for one Run.

The following must survive process restart when accepted/committed:

- lifecycle state;
- child Run topology;
- asks;
- accepted answers;
- buffered answers;
- pending cancellation/pause when the runtime contract says the request is durable;
- accepted injections;
- injection order;
- terminal outcome.

## Projection recovery

Projections are disposable.

After loss or corruption:

```text
event log
   |
   | replay
   v
new projection
```

Projection rebuild is always supported.

Cursor and hash verification are defined in `projections.md`.

## Suspended Runs

A suspended lifecycle state is durable even when no Python task is active.

The runtime must be able to recover:

```text
paused
waiting_answer
paused_waiting_answer
paused_answer_ready
```

and continue according to the state/action contract.

Executor-specific ability to restore local stack/locals is separate from Run lifecycle durability.

If an executor cannot resume after process loss, the capability contract must say so explicitly rather than losing the durable Run state.

## Buffered answers

`paused_answer_ready` requires the accepted answer to be durable.

After restart:

```text
PAUSED_ANSWER_READY
    -- resume() --> RUNNING with the same stored answer
```

The stored answer must not depend on in-memory state.

## Injection recovery

Accepted injections survive restart.

Their ordering remains unchanged.

Consumed/unconsumed state must be reconstructable.

## Pending control recovery

For control against a running Run, accepted durable intent survives process failure until:

- observed and applied;
- superseded according to precedence rules;
- made irrelevant by a terminal event.

A cancel accepted before worker death must not disappear because the worker died before reaching a safe point.

## Execution ownership

At most one actor may own advancement of a given Run segment at a time.

Ownership/lease mechanisms are implementation details, but the externally visible invariant is:

> No two workers may concurrently advance the same logical execution in a way that produces an event history that cannot be linearized.

## Lost worker

On worker loss, the runtime determines whether execution may be resumed/replayed/reclaimed according to executor capability.

Recovery must never:

- append two terminal outcomes;
- replay an accepted answer twice at the durable contract level;
- drop accepted injection;
- erase accepted cancel;
- create a second identity for the same Run.

## Terminal outcome

Exactly one of:

```text
completed
failed
cancelled
```

is the durable terminal outcome.

After terminal commit:

- pending control is irrelevant;
- further lifecycle actions are no-ops;
- injection is refused;
- child recovery may not revive the terminal parent as the same Run.

## Rebuild validation

After replay, the runtime may validate:

```text
final projection sequence == final event sequence
final projection hash == final event hash
```

when hash chaining is enabled.

## Disaster principle

If derived state is questionable, discard it.

Never repair the event log from a projection.

The event log repairs projections, not the reverse.
