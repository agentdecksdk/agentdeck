# Operating a run

The shape and naming of the surface that acts on a run which already exists. What each verb *does*
in each state is `design/run-lifecycle.md`'s (state × intent) table; this file does not restate it.

Decided 2026-08-14. Amends `delivery/decision-v3-entry-point.md`: ruling 2 and its no-daemon note.

## The namespace

`deck.runs.<op>(run_id)`. Plural, because `deck.run(...)` already means *start a turn* and the two
must not collide.

This is not the handle ruling 2 rejected. The run id stays an argument, there is no per-run object,
and there remains exactly one way to address a run. What changes is that eight ops which all take a
`run_id` are grouped under the noun they act on instead of sitting flat beside the eleven that do
not.

## The eight ops

| op | today | change |
|---|---|---|
| `status(run_id)` | `Deck.status` | raises `NotFoundError` instead of returning `None`, and reads the store's one-row lifecycle query instead of listing every run in the namespace |
| `events(run_id, from_seq=0, follow=False)` | **absent** | the read path. Retires `resolve_event_store()` plus a hand-built `RunContext` as the documented way to read a run |
| `list(status=None, namespace=None, limit=None)` | **absent** | `EventStorePort.list_runs` exists and no public caller can reach it |
| `pending(namespace=None)` | `Deck.pending` | returns typed requests, and only the ones a person can answer |
| `pause(run_id, reason=None)` | `Deck.pause` | honoured against a suspended run rather than dropped |
| `cancel(run_id, reason=None)` | `Deck.cancel` | honoured against a parked run rather than dropped (#229) |
| `resume(run_id, reason=None)` | `Deck.resume` | refuses with a message naming `answer` when the run is waiting for one |
| `answer(run_id, value)` | `Deck.answer` | the request validates the value (#235), and a pending cancel or pause is read first |

## Not on this surface

`deck.run(...)` and `deck.stream(...)` start a turn, so there is no run to address yet. `agents`,
`workflows`, `asgi()` and `session_for()` are not run-scoped either.

The author's side of these verbs, `ctx.run.*` inside a node or a tool, is undecided and out of
scope for this file.

## `tick` and `due` leave, and the sweep becomes internal

Both are public on today's `Deck` and neither survives here. Nothing in agentdeck calls them, so a
deployment that forgot a cron silently never woke a `sleep_until` and would silently never expire a
timeout.

The Deck owns a clock instead: it starts on `__aenter__`, stops on `__aexit__`, and closes what it
opened like every other resource it holds. One settings value sets the interval and most users
never see it.

This amends the no-daemon note in `decision-v3-entry-point.md`, which dropped `tick` on the grounds
that *"AgentDeck runs no daemon; a for-loop is not an API"*. The first half is what changes: an
opt-in task scoped to the deck's own lifetime is not the background service that ruling refused.
The second half still holds, which is why the sweep is not a public loop for a user to write.

Three consequences:

| | |
|---|---|
| two replicas must not double-fire | a resume goes through the store's conditional append, so exactly one caller wins and the loser is a no-op. The sweep needs no coordination of its own, only to tolerate losing |
| the sweep reads the log | it needs the durable list of open suspensions and their deadlines, so #212's single-inbox fix stops being cleanup and becomes a dependency: you cannot expire what you cannot enumerate |
| a short-lived process never sweeps | a serverless invocation opens the deck, takes a turn and closes before any interval elapses. Deadlines fire on whoever next holds a deck open, and that belongs in the docstring in those words |

**Amended 2026-08-15 (#303):** shipped narrower than the middle row above. The sweep still reads
each workflow's own checkpointer, exactly as `_tick`/`_due_resumes` already did — #212's
single-inbox fix is not a dependency here and stays open, deliberately out of scope. "Opt-in" in
this file's first paragraph turned out ambiguous: the sweep takes no flag and is on by default,
since an operator who forgot to opt in would silently reinstate the exact trap this closes. What
did land as designed: the task starts in `__aenter__`, is cancelled in `__aexit__`, and the
interval is the one settings value promised (`AGENTDECK_RUNTIME_SWEEP_INTERVAL_SECONDS`, 30s
default) on `RuntimeSettings` beside `stale_run_after_seconds`.

## Compatibility

Additive. `deck.runs.*` lands with the flat names delegating to it, deprecated later with a release
note rather than removed in the same change.

Two exceptions, which are removals: `tick` and `due_resumes`.

**Amended 2026-08-15 (#294):** shipped as a removal in one change instead, for all six — not
additive-then-deprecate. Twelve names for six verbs is the "two ways to do one thing" this repo
rejects, and the blast radius (37 call sites, all within this repo) was small enough to take in
one PR rather than carry a deprecation window for it.

## Open

| | |
|---|---|
| views | `runs.messages(id)` as a flat op, or `runs.read(id).messages` as a snapshot handle. The second reads the log once and folds it many ways, which is the real cost model, and it is the option ruling 2 leans against |
| a force terminate | no such verb today, and stale takeover is already automatic. Not added until something needs it |
| storage lifecycle | no `delete` or prune. Undesigned |
