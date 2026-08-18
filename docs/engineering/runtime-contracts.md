# Runtime Contracts

**Status:** Binding runtime behavior.

These are product guarantees. Implementations may change; these meanings should not change accidentally.

## 1. Run identity

A run has one durable identity for its lifetime.

Recovery, observation, resume, cancellation, and lookup must refer to the same logical run rather than creating shadow identities.

Application keys or namespaces may help locate a run but should not become competing run identities.

## 2. State is authoritative

Run state must have one authoritative source.

Handles may cache immutable identity, but live lifecycle state should be read from the authoritative runtime/store model when correctness depends on it.

## 3. Persist before observe

If an event is part of AgentDeck's durable event contract:

> A consumer that has observed the event can rely on it already being persisted.

Do not yield a durable event and persist it later.

## 4. One owner for durable ordering

Sequence numbers, timestamps, or equivalent persistence-owned metadata must be assigned by the component that makes the write authoritative.

Do not maintain competing counters or clocks across layers.

## 5. Terminal means terminal

Once a run/segment reaches a terminal outcome, no later event may contradict that terminal meaning.

If the runtime supports resumed segments, segment boundaries must be explicit enough that consumers can distinguish continuation from corruption.

## 6. Control has explicit semantics

Pause, cancel, answer, resume, timers, and similar control operations must have defined behavior for every relevant lifecycle state.

Ordinary races must not require callers to reverse-engineer scheduler timing.

Where only one caller can win, the winner is decided atomically by the authoritative state transition.

## 7. Safe points are contracts

Cooperative pause/cancel behavior occurs only at explicit safe points.

A safe point is part of runtime behavior, not an implementation convenience.

Adding, removing, or moving one may change user-visible behavior and should be reviewed accordingly.

## 8. Awaiting and observing are different

Observation should not accidentally own execution.

A caller that stops consuming an event stream should not stop a run unless the API explicitly defines that behavior.

Likewise, reading stored events must never advance execution.

## 9. Failures are both exceptions and facts

When a locally executing run fails:

- the caller should receive the meaningful failure,
- the runtime record should contain the corresponding terminal failure fact.

The two must not disagree.

## 10. Task ownership

Every execution task has an owner.

Closing the owning component must settle or cancel owned work in a defined order before tearing down resources that work still needs.

## 11. Liveness

Liveness mechanisms must not depend on accidental cooperation from neighboring components.

If a component requires a scheduling opportunity, lease renewal, queue drainage, or similar progress mechanism, it must own that mechanism.

Optional liveness infrastructure should degrade safely when the product contract allows fallback.

## 12. Recovery

Recovery must preserve logical meaning across process boundaries.

At minimum, recovery should not:

- reset durable ordering,
- create a new run identity,
- silently lose terminal state,
- allow two actors to win the same atomic transition,
- treat durable suspension as stale merely because no process currently owns a stack.

## 13. Sessions

When sessions enforce single active ownership, the rule must be atomic at the point where a new run/turn is admitted.

A second caller must not run concurrently merely because it observed stale state before another write committed.

## 14. Observability isolation

Observers and telemetry consumers must not unexpectedly redefine run correctness.

A slow or failing observer should be isolated unless a specific observer is intentionally configured as a correctness-critical sink.

## 15. Testing obligation

Every runtime invariant introduced here must have executable protection where practical.

See [`testing.md`](./testing.md).
