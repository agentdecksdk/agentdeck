# Execution and Adapters

Status: Proposed canonical design

This document defines how executors participate in Runtime 5.1 without owning lifecycle truth.

## Runtime owns lifecycle

Executors execute targets.

The Runtime owns:

- Run identity;
- durable events;
- lifecycle transitions;
- control semantics;
- projection inputs;
- terminality.

An executor may expose capability but may not define a competing lifecycle.

## Capabilities

Executors declare which runtime controls they can honor.

Capabilities are transition-relevant.

Examples:

- live suspension at a safe point;
- continuation after pause;
- continuation with an answer;
- input injection consumption;
- recovery/replay after process loss.

## Safe points

Executors define valid boundaries where pending control may be observed.

A safe point must be semantically safe for the executor's continuation model.

## Nested execution

Workflows and agents may create child Runs.

Child execution uses the same Run contract as top-level execution.

## Parallel execution

Executors may support multiple child Runs in parallel.

The runtime event model must preserve enough identity/causation to rebuild their execution tree.

## Replay

An executor that replays from a boundary must not claim to resume in-place.

The public lifecycle semantics remain the same; executor capability/recovery documentation states whether local stack/locals survive pause or process loss.

## Injection

Executors that support injection define input boundaries where queued injected values may be consumed.

Control priority is applied before injection delivery.

## Invariants

1. Executors do not own lifecycle state.
2. Executor capability cannot override lifecycle legality.
3. Runtime events remain authoritative.
4. Child Runs use the same identity/lifecycle/event contracts.
