# Events

Status: Proposed canonical design

This document defines the durable event model that acts as the single source of truth for Runtime 5.1.

## Source of truth

The durable append-only event log is the authoritative record of what happened.

Lifecycle state, execution topology, asks, answers, control outcomes, injection state, and observation views are derived from durable events.

No cache or projection may override the event log.

## Event envelope

Every event must carry enough information to support deterministic ordering, replay, and projection.

At minimum:

```text
event_id
run_id
sequence
timestamp
kind
payload
```

Where execution topology requires it, durable data must also expose or make derivable:

```text
parent_run_id
causation_id
correlation / invocation identity
```

Exact field placement is schema-specific, but replay must not depend on in-memory context.

## Ordering

Events for one Run have a strict durable sequence.

Sequence assignment and append are one logical commit.

No observer may see an event as committed before the store has committed it.

## Lifecycle events

Lifecycle events are exactly the events that change Run lifecycle state.

Canonical Runtime 5.1 lifecycle kinds:

```text
run.started
run.paused
run.resumed
run.interrupted
run.waiting_paused
run.waiting_resumed
run.answer_buffered
run.completed
run.failed
run.cancelled
```

A lifecycle fold uses these kinds to derive current state.

## Non-lifecycle events

Other durable events describe:

| Concern | Kind |
|---|---|
| Control request | `control.requested` |
| Control observation | `control.observed` |
| Answer refusal | `answer.refused` |
| Injected input | `input.appended` |
| Injection consumption | `input.consumed` (new) |
| Tool calls | `tool.call.started`, `tool.call.completed` |
| Model activity | `text.delta`, `thought.delta`, `message.completed`, `agent.changed` |
| Reports | `report` |
| Usage | `usage.reported` |
| Artifacts | `artifact.created` |

Ask metadata rides `run.interrupted`. Branch/fork metadata and observer diagnostics have no kinds yet and are open schema work, tracked in `migration-and-compatibility.md`.

These do not change Run lifecycle state unless explicitly designated as lifecycle events.

## Control observability

For deferred control against a running Run, the event model distinguishes three facts, with the kinds already in the schema:

| Fact | Kind |
|---|---|
| Request accepted | `control.requested` |
| Request observed at a safe point | `control.observed` |
| Resulting lifecycle transition | `run.paused` / `run.cancelled` |

This makes the difference between "requested" and "took effect" observable.

## Ask and answer

The log must durably preserve:

- ask identity;
- origin Run/branch;
- question/payload;
- options/answer contract when present;
- accepted answer;
- buffered answer state when applicable;
- refusals when part of the audit contract.

An executor must never receive an answer that cannot be reconstructed from the durable log.

## Injection

The log must durably preserve:

- each accepted injection;
- total order among injections for one Run;
- whether/when each injection was consumed or applied.

Receipt and consumption are distinct facts and are distinct kinds:

| Fact | Kind |
|---|---|
| Injection accepted | `input.appended` |
| Injection consumed by the executor | `input.consumed` |

`input.appended` already exists in the schema with no producer. Injection is its producer. `input.consumed` is new.

## Topology

The log must carry enough information to reconstruct the complete execution tree.

At child Run creation, this includes at least the child's durable identity and parent identity.

## Terminality

Exactly one terminal lifecycle event may commit for a Run:

```text
run.completed
run.failed
run.cancelled
```

After a terminal lifecycle event, no later lifecycle event for that Run is legal.

## Hash chaining

The event store may maintain a per-Run hash chain for integrity.

Conceptually:

```text
H0 = initial
Hn = HASH(Hn-1 || canonical_event_n)
```

The canonical serialization used for hashing must be versioned and deterministic.

The hash chain proves that a projection processed the same ordered event sequence. It does not by itself prove that projector logic is correct.

## Observers

Observers receive durable events or references to durable events.

Observer failure must not change the event log or runtime lifecycle.

Observation is downstream of persistence.

## Replay

A complete replay of the event log must be sufficient to rebuild:

- Run lifecycle state;
- execution tree;
- open and answered asks;
- buffered answers;
- injection inbox state;
- materialized runtime views.

Any runtime behavior that cannot be reconstructed from the log is a durability gap.

## Invariants

1. Append-only durable log is the authority.
2. Ordering is deterministic per Run.
3. Lifecycle events alone define lifecycle state.
4. Exactly one terminal event may commit.
5. Every accepted durable runtime action is represented in the log.
6. Replay never depends on process-local state.
