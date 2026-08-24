# Identity and Ownership

Status: Proposed canonical design

This document defines durable Run identity and execution ownership.

## Run identity

Every Run has one canonical durable `run_id`.

A child Run records `parent_run_id`.

Target name is not identity.

Repeated calls to the same agent/tool/workflow create distinct Run identities.

## Execution ownership

Exactly one runtime actor owns advancement of a Run segment at a time.

Observation, event reading, projection, and result waiting never own execution advancement.

## Parent/child ownership

A parent may create child Runs.

Children retain independent lifecycle state and durable identity.

Parent/child relationships are durable event facts and rebuildable into the execution tree.

## Process ownership

A process may temporarily own an executing task.

Process ownership is not Run identity and is not durable lifecycle state.

Worker loss must not change Run identity.

## Invariants

1. Run identity is durable and globally addressable within the runtime contract.
2. Parent/child edges are durable.
3. Observation never becomes execution ownership.
4. Worker replacement never creates a new identity for the same Run.
