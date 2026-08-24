# Observation

Status: Proposed canonical design

This document defines how runtime state is exposed to developers and operators without requiring them to replay the event log themselves.

## Layering

```text
Durable Event Log
      |
      v
Internal Projection
      |
      v
Runtime Views / Observation API
```

Application developers and operators normally read runtime state through views backed by official projections.

They should not need to implement their own lifecycle fold or execution-tree replay.

## Primary views

The runtime should expose concepts equivalent to:

```text
run.status()
run.tree()
run.events(...)
run.open_asks()
```

Exact API naming is owned by `public-api.md`.

## Snapshot view

A Run snapshot should expose current derived state such as:

```text
run_id
lifecycle state
terminal outcome if any
root target
children summary
open asks
paused/waiting branches
projection freshness metadata internally
```

## Tree view

A tree view exposes the full current execution topology.

It should make it easy to answer:

- what is running now;
- what is paused;
- what is waiting for answers;
- what has completed;
- what failed;
- what was cancelled;
- where each ask originated;
- which branches are concurrent;
- which child belongs to which parent.

## Live observation

The runtime may expose a live view or observer that emits:

- projection snapshots;
- projection diffs;
- tree updates;
- selected event-derived updates.

A live observer is downstream of durable state.

It never becomes execution owner.

## Observer vs projection

An observer is a delivery mechanism.

A projection is a deterministic read model.

They are related but not interchangeable.

A built-in "Run tree observer" may publish changes from the official Run-tree projection, but it does not own the tree truth.

## Events view

Raw events remain available for:

- audit;
- debugging;
- detailed traces;
- replay tooling.

Views are optimized for understanding current state.

Events are optimized for complete historical truth.

## Filtering

Observation APIs may filter out high-volume trace events when returning structural views.

For example, a Run-tree view does not need every model token delta.

It does need every event required to derive:

- state;
- topology;
- asks;
- answers;
- control outcome;
- injection state.

## Consistency

A returned authoritative state view must correspond to a known projection sequence.

If strong read-after-write semantics are promised, the runtime waits until the official projection has applied the relevant event before returning the view.

If eventual consistency is allowed for a particular surface, that surface must say so explicitly.

## Recovery

A view can disappear without data loss.

If the projection is rebuilt, the observation surface may temporarily lag or be unavailable, but it must not invent state.

## Invariants

1. Developers normally read official projections, not reimplement event folds.
2. Raw events remain available.
3. Observers do not own execution.
4. Projections do not become sources of truth.
5. Current-state views and historical trace are distinct products.
