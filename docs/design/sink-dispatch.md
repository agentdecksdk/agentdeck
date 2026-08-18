# Sink dispatch  -  the operational contract of `runtime/dispatch.py`

How a run hands an event to a sink, what happens when a sink is slow, wedged, dead, or recovering,
and the shutdown lifecycle. NFR-6's mechanism: **nothing on this path ever waits for a sink.**

Split out of `design/agentdeck-v2-architecture.md` §4.6 on 2026-08-14, carrying its dated amendments
of 2026-08-05 (twice) and 2026-08-06 (#89/#90). §4.6 keeps the headline and links here; on the
dispatch this file wins.

## The shape

**One queue and one consumer task per sink, not one task per event.** Handing an event over is a
queue put. A full queue yields exactly one loop turn and then drops the stalest event  -  the turn is
what separates a sink that is behind from a producer that has simply not suspended yet, since nothing
on the event path has to.

Because each sink is fed by a single consumer, `emit` is called one event at a time in submission
order and never re-entered. A consumer killed by a `CancelledError` escaping `emit` is replaced on
the next submit.

## Failure handling

| Condition | Behaviour | Constant |
|---|---|---|
| Sink raises from `emit` | Counted as a failed emit |  -  |
| `emit` does not return in time | Abandoned and counted as a failed emit, so a wedged sink reaches the same breaker a raising one does | `EMIT_TIMEOUT` 5s |
| `FAILURE_LIMIT` failures in a row | The sink is disabled | `FAILURE_LIMIT` |
| A disabled sink, after the cooldown | Offered one **real** event (never a synthetic probe): taking it re-enables the sink, failing it re-arms the cooldown from that failure | `BREAKER_COOLDOWN` 30s |
| Stack traces | One per sink per window, with unlogged failures counted in the next one | `LOG_WINDOW` 60s |

A dead endpoint therefore costs two emit attempts a minute instead of one per event. Events the open
breaker covered stay counted as drops  -  **nothing is replayed**. The cooldown is a deadline compared
against a monotonic clock rather than anything slept on, so no wait on a sink is added anywhere and a
sink's recovery is noticed by whichever submit happens to arrive after it.

*(Amended 2026-08-06, #89/#90: the breaker used to be one-way, and the failure log used to be
rate-limited by streak. Per-streak limiting bounded nothing for a sink failing every other event,
which builds no streak and trips no breaker either. The disable decision itself was untouched by that
change.)*

## Lifecycle

| Call | Meaning |
|---|---|
| `flush(timeout)` | Wait for queued events to be *attempted*; the dispatch stays usable |
| `close(timeout)` | Terminal  -  afterwards a submit is counted as a drop instead of starting a fresh consumer, and events still queued or in flight are added to the drop count so the counters match the loss reported in the log |
| `Runtime.drain()` | Flushes the queues and then stops the consumers, racing each flush against its consumer so one dead consumer cannot hang shutdown for every other sink. Called by the composition root at shutdown, never per event |

The shutdown flush has its own deadline (`SHUTDOWN_TIMEOUT`, 10s) and gives up rather than waiting.

*(Amended 2026-08-05, hardening: every wait on this path is bounded because a sink blocked inside
`emit` defeated every exit condition the flush had and hung shutdown outright. A `CancelledError` a
sink raises from its own `emit` is a counted failure rather than a silently dead consumer; only a
genuine cancellation  -  `close`, loop shutdown  -  ends a consumer, and that path replaces it on the next
submit.)*

## Guaranteed delivery is a non-goal

A blocking/backpressure policy was built and removed before merge: no sink implementation needs it,
and the only ways to keep it were a producer that waits forever or an amendment to NFR-6. **Sinks are
a lossy tap**; a consumer that must see every event reads the event store. If a real sink ever demands
delivery, it is added on top of this  -  never by making a run wait.

Drops and failed emits are counted per sink and logged, so loss is observable rather than silent.

## Liveness

`SinkDispatch.submit`'s `await asyncio.sleep(0)` on a full queue is the canonical case of
`coding-standards.md` §6's rule that liveness is self-supplied, never borrowed: the dispatcher gives
its own consumer the turn rather than trusting the store or the engine to suspend first (#87).
