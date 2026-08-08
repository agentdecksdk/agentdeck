# ADR-D11 — The store assigns `seq` and `ts`

**Status:** accepted
**Date:** 2026-08-08 · **Relates to:** ADR-D5, design doc §4.2 · §5, `core/ports/store.py`,
`runtime/service.py`, coding-standards §7
**Supersedes:** the sentence *"A store never reads a clock — the caller stamps `ts`, so only the
caller's idea of 'now' can be compared to it"* (`EventStorePort.claim_start`), and the division of
labour in the design doc that puts envelope stamping wholly in the Runtime.

---

## 1. Decision, in one line

**Assigning a run's next `seq` and stamping `ts` happen inside the store, in the same indivisible
operation that persists the event.** No component outside a store holds a sequence counter or
decides an event's time.

Everything else about the split stands: engines yield payloads and never see an envelope, and the
Runtime still owns *policy* — what "open run" and "resumable" mean live in `core/status.py`, which
every adapter imports.

## 2. Why

`seq` has two jobs (`core/events.py`): it is the ordering authority *and* the loss check. The
second only works if the sequence is dense — a gap means an event is missing. Today the Runtime
holds the counter, and allocation is separated from the write by an `await`:

```python
yield await self._record(payload, spec, ctx, next(seq))   # seq taken here, append may fail
```

Measured on the committed code, with a store that refuses one append mid-run:

```
in the log       : [(0, 'run.started'), (1, 'text.delta'), (3, 'run.failed')]
check_contiguous : [2]        ← a gap no refetch can ever fill
check_terminal   : None       ← the run is otherwise correctly closed
```

The consequence is already known and documented — `_drain`'s docstring says *"the `seq` that
report consumed stays spent, so the log shows a gap … not this arm's to close"*, and
`test_runtime_service.py` pins it. But it makes the module's own promise false:

> `runtime/service.py:5` — *"a consumer that spots a `seq` gap can always refetch it"*

Both cannot hold. A consumer that sees a gap today cannot tell "an event was dropped in transit,
refetch it" from "the store hiccupped an hour ago, this hole is permanent, refetching will never
converge."

The counter is also spread thin: seven functions take `(spec, ctx, seq)` purely to pass them on,
and **ten call sites** each decide when to advance it. Nothing owns the number, which is why the
failure path has to guess whether the one it took was used.

Moving assignment into the store removes the question rather than answering it. A seq that is
allocated and persisted in one step cannot be allocated and not persisted.

## 3. What this buys

- **A gap means an event was genuinely lost.** `check_contiguous` becomes the loss check it is
  documented to be, and the refetch promise becomes true.
- **`claim_resume` loses half its contract.** Its stale-`seq` guard exists because a caller stamps
  before claiming; when the store assigns, that race cannot occur. One question remains: is this
  run `WAITING_HUMAN`?
- **`last_seq` comes off the port.** Its only three callers (`service.py:153, 384, 452`) are seq
  arithmetic that stops existing. Postgres keeps a private equivalent for its own assignment.
- **One clock per store, not one per worker.** N workers sharing a Postgres stamp with N clocks
  today; `stale_before` comparisons are only as good as the agreement between them.
- **The Runtime shrinks.** No counter, no `Iterator[int]` threaded through seven signatures.

## 4. What it costs — accepted deliberately

- **One clock seam becomes four.** `service.py:93` is currently the only injected clock in the
  system, and it is how golden snapshots pin `"ts"`. Each adapter gets its own, injectable.
- **Stores construct envelopes.** SQLite persists an opaque JSON blob today; it will build the
  `Event` after knowing the seq. Adapters import and construct core models. This does **not**
  weaken the engine boundary: engines still yield `AsyncGenerator[KnownPayload]`, which has no
  envelope fields, so an engine still cannot forge `seq` or `tenant`.
- **`append` gains a return value.** The Runtime yields what it wrote, so it needs the finished
  events back.
- **`stale_before: datetime` becomes `stale_after: timedelta`.** A caller that no longer owns the
  authoritative clock cannot compute a cutoff in it.
- **Four implementations must each prove atomicity.** That is the point of the decision, and §6 is
  how it is enforced rather than asserted.

## 5. The port, after

```python
async def append(self, log_key, payloads: Sequence[KnownPayload], ctx, origin: str) -> list[Event]
async def claim_start(self, log_key, opening: RunStarted, ctx, origin,
                      stale_after: timedelta) -> tuple[SessionClaim, Event | None]
async def claim_resume(self, log_key, run_id, resumed: RunResumed, ctx, origin) -> Event | None
```

`read`, `read_run`, `list_runs` and `run_status` are unchanged. `last_seq` is removed.

**The two claims stay, as named methods.** They are not extra operations — they are conditional
appends, and they are the only place mutual exclusion can live without adding a second piece of
infrastructure. Measured: two workers that read "session idle" and then append both open a run on
one session; `claim_start` refuses one of them. A `transaction()` port that let the Runtime run the
decision itself would have to unify a lock (SQLite `BEGIN IMMEDIATE`), a transaction (Postgres) and
optimistic retry (Redis `WATCH`/`EXEC`) — every caller would have to be written for "this block may
run twice." Rejected: the abstraction is leakier than two contracts stated once.

## 6. Per-backend mechanism — what each must guarantee

| store | seq | atomicity |
|---|---|---|
| memory | per-run counter | no `await` between assign and append |
| sqlite | `COALESCE(MAX(seq), -1) + 1` in `INSERT…SELECT` | inside the existing `BEGIN IMMEDIATE` |
| postgres | the same `INSERT…SELECT` | transaction + the existing `UNIQUE` index |
| redis | `INCR` on the per-run seq key it already keeps | inside the existing `MULTI`/`WATCH` |

The `UNIQUE (tenant, log_key, run_id, seq)` indexes stay. They are no longer the guard — they
become the proof that assignment is correct.

**Enforcement:** the shared store contract suite grows a concurrency case — many tasks appending to
one run at once, asserting the result is contiguous with no duplicates. A backend that cannot pass
it must not implement the port.

## 7. Consequences to land with the change

- `test_runtime_service.py`'s gap assertion flips `== [2]` → `== []`, and its comment inverts from
  "a known consequence" to "no longer possible".
- The `_drain` paragraph ending *"not this arm's to close"* is deleted; it stops being true.
- `coding-standards.md` §7 and the architecture doc both describe the current division and need
  updating.
- CHANGELOG: logs no longer carry gaps after a dropped report or a transient append failure.

## 8. What was considered and rejected

- **Keep the counter in the Runtime, own it in one object** (a per-run writer that advances only
  after a successful append). Fixes the gap for a fraction of the cost, inside one file. Rejected
  only because it leaves the counter outside the store, which this decision moves on purpose; it
  remains the fallback if the port change proves too invasive.
- **Optimistic append-then-reconcile** — write first, discover you lost, clean up. The vocabulary
  exists (`RunResumed`'s docstring already describes recording an interrupt again as the rollback).
  Rejected: a loser that crashes between its write and its cleanup leaves an open run
  indistinguishable from a real one, wedging the session for a staleness window — manufacturing
  more of the problem the takeover machinery exists to mop up.
- **Dropping the duplicated `kind`** from the wire. Separate decision, deliberately not taken:
  measured, the released `v2.0.0b4` reader cannot read a row missing the payload copy.
