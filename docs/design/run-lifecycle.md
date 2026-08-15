# The run lifecycle

Which event moves a run's state, what is true of each state, and what a request does to a run
sitting in one. Audited against the tree at `da46439`, 2026-08-14.

`design/agentdeck-v2-architecture.md` §4.4 summarises this file; on the lifecycle this file wins.

The log *is* the state — `core/status.py` folds a run's lifecycle events and there is no status
table, so `status_of` after a restart returns whatever the log says.

## The machine

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> RUNNING: run.started
    RUNNING --> PAUSED: run.paused
    PAUSED --> RUNNING: run.resumed
    RUNNING --> WAITING_HUMAN: run.interrupted
    WAITING_HUMAN --> RUNNING: run.resumed
    RUNNING --> COMPLETED: run.completed
    RUNNING --> FAILED: run.failed
    RUNNING --> CANCELLED: run.cancelled
    PAUSED --> CANCELLED: run.cancelled
```

Seven states and the seven `LIFECYCLE_KINDS` that move them, as built; no other kind in the
schema moves a state.

## Per-state properties

Held today as three collections in two modules — `RESUMABLE_STATUSES` and `TERMINAL_STATUSES` in
`core/status.py`, `SUSPENDED_KINDS` in `runtime/service.py` — each carrying a third of this table.

| state | terminal | suspended | resumes with | observable |
|---|---|---|---|---|
| `PENDING` | no | no | — | **no** |
| `RUNNING` | no | no | — | yes |
| `PAUSED` | no | yes | nothing | yes |
| `WAITING_HUMAN` | no | yes | a value | yes |
| `COMPLETED` · `FAILED` · `CANCELLED` | yes | no | — | yes |

## What a request does to a run in each state

A *total* function of (state, intent) — the half that exists in no module. A missing pair is how a
request gets accepted and then read by nothing.

| state | `cancel` | `pause` | `resume` | `answer` |
|---|---|---|---|---|
| `RUNNING` | at next safe point | at next safe point | no-op | refuse |
| `PAUSED` | honored by next resume | no-op | continue, lift the pause | refuse |
| `WAITING_HUMAN` | **terminate** ‡#229 | lift — the answer is the continuation ‡ | **refuse**, naming `answer` ‡ | continue with the value |
| terminal | no-op | no-op | no-op | no-op |
| `PENDING` | refuse | refuse | refuse | refuse |

‡ Ruled, not built: the three `WAITING_HUMAN` cells are what should happen. What happens today is
in *Drift* below — the rest of the table is behaviour.

Two rules govern the table. A ruling names the rows to append **and** what becomes of the request
(`consume` or `leave` — `consume` needs the port method §4.5 records as missing). And **every
control-port read ends in an event or an explicit no-op, never in silence**; silence is
unobservable, which is #229 and its two unfiled siblings at once.

## Routing a request

The rule the whole design hangs off: **the log is the only place run state lives, and appending is
the only way a decision becomes true.** Nothing holds a status field, nothing caches a fold, and
the module that owns the rules holds no state at all — delete its memory between two calls and
nothing is lost.

Five steps, in this order.

| | | |
|---|---|---|
| 1 | claim | the conditional append that makes this caller the only actor on the run |
| 2 | fold | read the log, derive the state. `runtime/service.py:259` already states why this must follow the claim: an intent read before it "could belong to somebody else's turn" |
| 3 | intent | read the control port. A request, never a record |
| 4 | decide | `POLICY[state, intent] -> Ruling` |
| 5 | append | the ruling's events. Nothing else makes it real |

A `Ruling` carries three things, and the third is the one that is currently implicit:

| field | |
|---|---|
| what to append | the events, or none |
| what becomes of the intent | `consume` or `leave`. `resume_run` hand-rolls this today and documents why an unconditional write "would overwrite, and silently destroy, a cancel that arrived while the run was suspended" |
| why | one sentence, which is simultaneously the error message, the docs cell and the test name |

`consume` needs `ControlPort.consume(run_id, expected) -> bool`, recorded as missing in
`agentdeck-v2-architecture.md` §4.5.

## The declaration

One table per axis, in `core/status.py` — not a new module, because 23 import statements across 20
files make a rename churn, and that file already is this subject.

| | replaces |
|---|---|
| `STATES` — terminal, suspended, resumes-with, per state | `RESUMABLE_STATUSES`, `TERMINAL_STATUSES` (`core/status.py`) and `SUSPENDED_KINDS` (`runtime/service.py:56`): three collections in two modules, each holding a third of one table |
| `TRANSITIONS` — event kind to state | `_KIND_TO_STATUS`, unchanged in substance |
| `POLICY` — (state, intent) to `Ruling` | two `if pending.verb is …` branches in `resume_run`, the `status=PAUSED` filter inside `_paused`, and, for the cells nothing implements, silence |

Every derived set stays derived, which is the pattern `TERMINAL_STATUSES` already uses: a terminal
kind added without a transition raises at import rather than answering wrongly at runtime.

`POLICY` is **total**: six states after `PENDING` goes, four intents, twenty-four cells, asserted
at import. A missing ruling becomes a missing key, and a missing key is a test failure rather than
a request that is accepted and then read by nothing.

`tests/core/test_vocabularies_agree.py` is where that assertion belongs. It exists for exactly this
discipline and already guards the kind tables against the schema.

## Drift

| claim | truth in the tree |
|---|---|
| §4.4: transitions are "guarded in one place (`core/status.py`)" | Guarded in five: `RESUMABLE_STATUSES` and `TERMINAL_STATUSES` (`core/status.py:37,57`), `SUSPENDED_KINDS` (`runtime/service.py:56`), two `if pending.verb is …` branches in `resume_run` (`runtime/service.py:260,268`), and the `status=PAUSED` filter inside `_paused` (`runtime/service.py:362`) |
| §4.4: `CANCELLED` is "reachable from … `WAITING_HUMAN`" | It is not. `Runtime.resume` never polls the control port, so a `cancel` recorded against a parked run is read by nothing (#229); a `pause` vanishes the same way |
| `Deck.resume` on a parked run reports something | It returns `[]`: `_paused` lists only `PAUSED` runs, so the state is never seen |
| `PAUSED` is reachable for any run | Not for a workflow run — the langgraph adapter never calls `gate.checkpoint()` (#128) |

## Declared, never produced

| declaration | why nothing produces it |
|---|---|
| `RunStatus.PENDING` | A run with no events has no rows to list, so no caller can observe it — a deletion candidate |
| `SafePoint`'s `tool_dispatch`, `node_boundary` | Every `checkpoint()` call site is bare, so `stream_item` is the only value emitted |
| `RunFailed.error_code`'s `tool_error`, `budget_exceeded`, `deadline` | Only `engine_error` and `cancelled_hard` are ever constructed, and a tool that raises ends the run `completed` (#250) |

## `WAITING_HUMAN` is misnamed

`WAITING_ANSWER` pairs the state with the verb that leaves it.

`sleep_until` parks here, so a wall-clock wait is recorded as a human one, and
`RunInterrupted.reason` defaults anything unrecognised to `"human"`
(`adapters/engines/langgraph/engine.py:331`) — including a timer payload, which carries no
`reason` at all.

The enum rename is an ordinary API break: the value is in no golden file and no snapshot, because
status is derived, so `coding-standards.md` §7 does not apply. `RunInterrupted.reason`'s literal
*is* in the schema, and renaming it is a separate versioned change.
