# Migration and Compatibility

Status: Proposed canonical design

This document describes how existing runtime behavior migrates to the Runtime 5.1 contract.

## Principle

Runtime 5.1 documents define the target design.

Existing code is compared against this contract and changed where necessary.

The design is not constrained to preserve an accidental implementation detail unless compatibility is explicitly chosen.

## Expected migration areas

Likely changes include:

- lifecycle enum expansion;
- new lifecycle events for paused waiting and buffered answer states;
- atomic control claims;
- cancellation precedence over pause;
- explicit ask identity for parallel asks;
- durable answer buffering;
- ordered injection inbox;
- branch/fork causation metadata and observer diagnostic kinds, neither of which has an event kind yet;
- official Run-tree projection;
- projection cursor/hash integrity;
- public tree/view API;
- recovery of new durable states.

## Compatibility decisions

Each public compatibility decision must be explicit:

- preserved;
- deprecated;
- replaced;
- removed.

Do not silently keep two overlapping runtime contracts alive.

## Schema changes

Event schema changes are versioned and migration-tested.

A projection version change may require rebuild rather than data migration.

## Rollout

Recommended order:

1. event/schema support;
2. lifecycle/state machine;
3. atomic control behavior;
4. ask identity and answer buffering;
5. injection;
6. tree/projection;
7. observation/public API;
8. migration cleanup.

## Verification

Migration is complete when production code and tests derive from and conform to the canonical Runtime 5.1 documents.
