# The event store's two conditional appends

`claim_start` and `claim_resume`: what each one tests, why the condition and the write have to be
one operation, and what the staleness window costs whoever operates it.

Split out of `design/agentdeck-v2-architecture.md` §4.5 on 2026-08-14, carrying its dated
amendments of 2026-08-05, 2026-08-06 and #83. §4.5 keeps the headline and links here; on the claims
this file wins. Signatures are governed by `design/adr-d11-store-assigns-seq-and-time.md`.

**One rule underneath both:** the write that publishes a transition is the write that tests for it.
That is what carries mutual exclusion across processes rather than merely across tasks — two servers
sharing a store would both read an idle session and both open a run on it.

## The two claims

| claim | tests | on losing |
|---|---|---|
| `claim_start(log_key, opening, ctx, origin, stale_after) -> (SessionClaim, Event \| None)` | this log has no open run | `SessionClaim.held_by` names the holder, nothing is written, `Event` is `None` |
| `claim_resume(log_key, run_id, resumed, ctx, origin) -> Event \| None` | `run_id` is `WAITING_HUMAN` | `None`; the loser reads the `RUNNING` the winner's append published |

Neither raises on losing — two turns at once is a double-clicked send button, so the refusal is
**data**. Only an unreachable store raises (`StoreError`): it cannot know whether anybody holds
anything. SQLite makes both indivisible in one `BEGIN IMMEDIATE` transaction; the dict store gets it
for free, since neither the fold nor the append suspends. A store that cannot do both in one step
must not implement the port.

## When a session is busy

A session is busy while one of its runs has recorded a lifecycle transition and not a terminal one —
**`WAITING_HUMAN` included**, because an interrupted run still owns the engine thread it will resume
on, and a second run against that thread would overwrite the checkpoints the resume needs. A run
with no transition at all is `PENDING`, which no store can tell from a run it never saw, so it holds
nothing — the line `list_runs` already draws.

Busy-ness is **derived from the log**. No lease table, no TTL row, no heartbeat — which is what keeps
run status a projection rather than a second store (`design/run-lifecycle.md`).

The Runtime turns a refusal into `errors.SessionBusyError`, naming the session and the holding run,
raised from `run()` **before any event is yielded**: a caller that asked for a turn and got an empty
stream could not tell it from a turn that produced nothing. Over HTTP the decision has to be made
before the response begins — a `StreamingResponse` has already committed `200` and
`text/event-stream`, so a refusal after it reaches a client only as a body that stops — so the
surface pulls the opening event and answers **409** with the holding run named.

A client that disconnects between the claim committing and receiving anything has its run closed as
`run.cancelled` by the cancellation arm, rather than leaving it open and holding the session.

**Queueing the loser is deliberately not built.** In-process queueing does not survive a second
worker, and a store-level queue is a lease with ordering — fencing, expiry, stale-entry reclamation —
real distributed machinery for a problem a client retry already solves.

## The staleness window

The one state the log cannot distinguish is a run whose process was **killed outright**: every
graceful exit closes its run, so silence is all that is left to go on.

`AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS`, one hour by default — a settings value, not a constant,
and required to be **positive** (at zero a run's own opening event is already stale to the next
caller a moment later, which turns the guarantee back into a race). An open run whose last event of
any kind is older than that — so a streaming turn keeps resetting it — stops holding its session,
comes back in `SessionClaim.overridden`, and is closed by the claiming turn as
`run.failed` / `cancelled_hard` under **its own** `origin`.

The takeover is logged at WARNING and can always be premature; a permanently wedged session is the
worse failure. Failing to *close* the overridden run is not worth failing the new turn over either —
the claim is already committed, so the close is dropped with a log line and the next turn, meeting
the same stale run, tries again.

| Consequence for whoever operates this | Detail |
|---|---|
| A session a killed process left claimed | Refused until the window elapses |
| A run waiting on a human for longer than the window | Closed as failed the next time somebody starts a turn on that session, so an installation with slower approvals raises the setting |
| The window is **not skew-free** | Each worker compares its own `clock()` against a `ts` a peer stamped, so across machines the effective window is the configured one minus the worst clock skew, and a worker running more than a window fast would take over live sessions on sight. Keep the fleet on NTP and treat skew as eating into the budget |
| **The window has a floor the code cannot enforce** | Shortened below the longest quiet stretch of a healthy turn, an open run looks abandoned while it is still working, so the next turn takes the session from a live one and both run on the same conversation — one turn per session stops holding at all, rather than merely cleaning up early. How long a turn may be quiet is a property of the deployment, so only positivity is validated; the rest is the setting's docstring and this note |

An explicit operator "abandon run" route can follow if it is ever asked for.

## Why `seq` is unique per run

A premature takeover leaves the stepped-over run alive and able to write again at a `seq` its own
closing event already used — the one corruption `check_contiguous` cannot see, since it looks for
gaps and not duplicates. So `(tenant, log_key, run_id, seq)` is **unique** in the SQLite log (the
per-run index carries the constraint) and the dict store refuses the same pair in `append`: a
resurrected run fails loudly with `StoreError` at its next write instead of putting two different
events at one `seq`, which would make every consumer's refetch of that `seq` a coin toss. Such a run
does then end twice in the record, which is detectable — unlike the duplicate. `seq` stays per run,
so two runs of one session both counting from 0 is unaffected.

**Consequence for tests, and for anything that shells a second turn into a live session:** two
concurrent runs in one log are no longer reachable through the Runtime, only through a stale
takeover. The engine-side lock protecting a session's execution state
(`adapters/engines/openai_agents/reconcile.py`) is no longer the first line of defence, but it is
still the last, and keeps its own test.

## SQLite specifics *(amended 2026-08-06, as built)*

Both SQLite adapters — the event log and the control-signal table — open in WAL mode with an explicit
5-second busy timeout, and translate `sqlite3.Error` into `errors.StoreError` at every public method,
so no library type crosses a port (`coding-standards.md` §5 at the store boundary). A losing
`claim_resume` waits for the winner's transaction to commit and then reads the `RUNNING` status it
published, instead of meeting a raw `database is locked`. A lock held past the busy timeout is a
`StoreError` — a store nobody can write to, deliberately not folded into the loss that means somebody
else won.

| Operational consequence | Detail |
|---|---|
| WAL sidecar files | `-wal`/`-shm` sit beside each database and belong to it for backup and deletion |
| WAL needs cross-process shared memory | A SQLite store on NFS or SMB is unsupported; that deployment wants Redis or Postgres |
| Converting a file *into* WAL needs an exclusive lock | SQLite refuses it outright while a peer is writing, so a connection that cannot switch keeps the mode the file has: slower under contention, never wrong, never a failure to open |

## Divergences from what shipped

ADR-D11 moved `seq` and `ts` assignment into the store, which rewrote both signatures. The rulings
above are unaffected; what changed is who fills the envelope.

| §4.5 as written 2026-08-05/06 | Truth in the tree |
|---|---|
| "the store never stamps an event, since the Runtime is the only assigner of `seq`" | Reversed by ADR-D11: the store assigns `seq` and `ts` in the same indivisible step that persists the event |
| `claim_start(log_key, event, ctx, stale_before)` | `stale_after: timedelta`, because the caller no longer owns the clock the comparison is made in |
| `claim_resume(...) -> bool` | `-> Event \| None` — the Runtime yields what the store wrote |
| The resume claim also tested that the event's `seq` was still the run's next one | Status is the whole condition now; the caller no longer holds a `seq` to go stale |
| `SessionClaim.overridden` carries run ids | It carries each abandoned run's last event, so the closer can build that run's own `RunContext` and call the ordinary `append` (ADR-D11 §5) |
