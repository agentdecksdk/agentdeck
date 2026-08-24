# Projections

Status: Proposed canonical design

This document defines derived runtime state, incremental materialized views, integrity verification, and rebuild behavior.

## Definition

A projection is a deterministic derived view of the durable event log.

Examples:

- current lifecycle state;
- full Run tree;
- open asks;
- answered asks;
- injection inbox state;
- per-node execution status;
- operator-facing Run snapshot.

A projection is not a source of truth.

It may be deleted at any time and rebuilt from the event log.

## Why materialize

Replaying the full event log for every read is correct but inefficient.

Runtime 5.1 should maintain incremental projections so reads can be served from already-folded state.

```text
EVENT LOG
   |
   v
PROJECTOR
   |
   +--> Run state view
   +--> Run tree view
   +--> Open ask view
   +--> Injection inbox view
```

## Incremental application

Each projector tracks a durable cursor.

At minimum:

```text
last_applied_sequence
last_applied_hash
projector_version
```

For every new event:

1. verify sequence continuity;
2. verify expected previous hash when hash chaining is enabled;
3. apply the event deterministically;
4. advance the projection state;
5. persist the new cursor/hash atomically with the derived update.

## Hash chain verification

A projection may maintain the same per-Run event hash chain as the event log.

Conceptually:

```text
expected_hash_n = HASH(expected_hash_n-1 || canonical_event_n)
```

After applying event `n`, the projection records the resulting hash.

If projection hash and authoritative event-log hash differ at the same sequence, the projection is invalid.

The projection must not attempt to patch around the mismatch silently.

It is rebuilt.

## What hash verification proves

Hash agreement proves:

- the same events were processed;
- in the same order;
- using the same canonical event bytes/version.

It does not prove that the projector implementation produced the logically correct tree.

Projector correctness still requires deterministic implementation and tests.

## Rebuild

A projection must support full rebuild:

```text
delete projection
replay event log from sequence 0
recreate derived state
verify final cursor/hash
publish as valid
```

A rebuild may happen because of:

- cache loss;
- projector bug fix;
- projector version upgrade;
- detected sequence gap;
- checksum mismatch;
- storage corruption;
- migration.

## Projection versioning

Projector logic is versioned.

A stored projection records the projector version that created it.

If runtime code requires a newer incompatible projector version, the old projection is not trusted and is rebuilt or migrated explicitly.

## Freshness

A projection read should be able to report or internally verify:

```text
projection_sequence
authoritative_sequence
```

A caller-facing API may hide this when the runtime guarantees synchronization before returning.

Internal tooling should be able to inspect lag.

## Invalid projection behavior

On:

- sequence discontinuity;
- hash mismatch;
- impossible state transition;
- unknown required event;
- projector version mismatch;

the projection is marked invalid.

Reads that require authoritative current state must not silently serve known-invalid projection data.

## Run tree projection

The canonical Run-tree projection should maintain, at minimum:

```text
root Run
nodes by run_id
parent/child edges
per-node lifecycle state
open asks
answered asks
buffered answers
injection inbox summary
terminal outcome
projection cursor/hash
```

Heavy trace payloads should remain in the event log and be fetched on demand.

## Storage

Projection storage may be:

- in-memory;
- local database;
- shared database;
- dedicated read store;

depending on deployment needs.

The semantic contract is independent of storage technology.

## Single source of truth invariant

A projection may make reads faster.

It may never become authoritative.

If the event log and projection disagree, the event log wins and the projection is invalidated/rebuilt.
