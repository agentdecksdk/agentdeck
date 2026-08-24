# Gaps

Every ruling in this contract set checked against the tree at `5531eff`, one box per ruling.

Not a contract document: nothing here decides anything, and the decision map does not list it. It
is the verified instantiation of `migration-and-compatibility.md`'s expected migration areas, and
it goes stale the moment code moves.

`[x]` holds in the tree today, with the evidence. `[ ]` is work. A `[ ]` tagged **conflict** is
built and built differently, which is worse than absent: the code has a rule that has to be
withdrawn, not a hole that has to be filled.

## The four conflicts

| # | Contract | Tree | Where |
|---|---|---|---|
| C1 | Pending control coalesces by strength; cancel may not be erased by pause | One slot, last write wins: `signal()` records "replacing whatever was pending" | `core/ports/control.py:27`, `adapters/control/sqlite/port.py` |
| C2 | `waiting_answer` + `pause()` buffers the answer into `paused_answer_ready` | The answer is **refused** while a pause is pending, and both intents are kept by making the answerer retry | `core/status.py:243` `_WAITING_ANSWER_ROW` |
| C3 | Pending control applies immediately once `run.interrupted` commits | `POLICY` is read at the next claim, so a pending pause sits until something resumes or answers. Cancel is the exception and already matches | `runtime/service.py:424` `signal`, `:479` `_cancel_suspended` |
| C4 | Parallel branches may hold open asks simultaneously | `ctx.parallel()` refuses concurrent `ctx.ask()` inside one run (#414): one run parks on one question | `core/context.py:386` |

C4 is the narrow one. The contract's model is per Run and the code's workaround is the contract's
own answer ("give each question a child run of its own"), so what is missing is the ruling that
says so, not the mechanism.

## lifecycle.md

- [x] Six of the eight states exist: `running`, `paused`, `waiting_answer`, `completed`, `failed`, `cancelled` (`core/status.py:32`)
- [ ] `paused_waiting_answer`
- [ ] `paused_answer_ready`
- [x] Seven lifecycle event kinds and the fold that derives state from them (`core/status.py:88` `TRANSITIONS`, `:291` `status_of`)
- [ ] `run.waiting_paused`
- [ ] `run.waiting_resumed`
- [ ] `run.answer_buffered`
- [x] Terminal states are absorbing, and a terminal kind without a transition raises at import (`core/status.py:104`)
- [x] There is no `created`/`pending`/`queued` state, and the contract's reason is the tree's (`core/status.py:32`, `run-lifecycle.md` "Declared, never produced")
- [x] State x action matrix, as `PRECONDITIONS` over `RunStatus` x `Operation` (`core/status.py:179`)
- [ ] Two matrix rows and their cells, for the two new states
- [ ] **conflict** C2: `waiting_answer` + `answer()` under a pending pause refuses instead of buffering
- [x] A buffered answer is never overwritten, in the degenerate case: `claim_resume` is a conditional append, so one answer wins (`core/ports/store.py:182`)
- [x] `resume()` does not synthesize a missing answer: `RESUME` against `waiting_answer` is refused, naming `answer` (`core/status.py:137`)

## control-and-concurrency.md

- [x] Safe points as an execution boundary rather than a state, with `ctx.safepoint()` and a polling `Gate` (`core/context.py:199`, `core/control.py:110`)
- [x] `SafePoint` is a closed durable vocabulary (`core/events.py:197`)
- [ ] The contract lists five boundary kinds against the tree's three, and `tool_dispatch` is declared and never produced (`run-lifecycle.md` "Declared, never produced"). Either the contract narrows to what exists or the executors grow the rest
- [ ] An explicit `ctx.safepoint()` call records `stream_item`, so the log cannot tell an author's boundary from a stream one
- [x] Pause and cancel against a `running` run are accepted before their effect, as recorded intent (`core/ports/control.py:31`)
- [x] Cancel against an already suspended run applies immediately through an atomic claim, not a safe point (`runtime/service.py:479`)
- [ ] **conflict** C3: pause against an already suspended run stays recorded and moves nothing
- [x] Atomic claims rather than check-then-write, on both the store and the control port (`core/ports/store.py:123` `claim_start`, `:182` `claim_resume`, `core/ports/control.py:40` `consume` compare-and-set)
- [x] A lost claim re-reads instead of acting on stale state (`core/ports/control.py:40`, `runtime/service.py:479`)
- [x] No accepted action resolves in silence: every cell of `POLICY` is an event or an explicit no-op, pinned by a test (`core/status.py:191` `Action`, `tests/core/test_run_lifecycle_tables.py:121`)
- [ ] **conflict** C1: precedence `NONE < PAUSE < CANCEL` has no implementation. The port replaces
- [x] Duplicate actions are idempotent per state, and exactly one terminal event commits (`core/status.py:179`, `core/ports/store.py:123`)
- [x] Capability and legality are separate dimensions, joined only at the edge (`core/status.py:369` `can_of`, `core/ports/executor.py:40` `suspendable`)
- [x] Capability exposed to callers is an advisory snapshot (`Run.can`, `core/status.py:361` `Controls`)
- [ ] The contract does not rule on cancel cascade to child runs, which the tree does: cancel cascades, pause does not (`runtime/service.py:469`). A contract ruling is owed, in `execution-tree.md` or here

## input-and-suspension.md

- [x] `ctx.ask(question, options=..., **fields)` as an execution primitive that suspends in place (`core/context.py:436`)
- [x] Ask identity is durable, spelled `interrupt_id` (`core/events.py:179`, minted at `core/context.py:468`)
- [ ] The contract spells it `ask_id`. One name has to lose; renaming a durable field is a schema change
- [x] Options travel on the interrupt and are validated before the continuation claim commits (`runtime/service.py:1202`)
- [x] An invalid answer moves no lifecycle state and the run stays waiting, recorded as `answer.refused` (`core/events.py:317`, `runtime/service.py:327`)
- [x] An accepted answer is durable in the same write that transitions the run (`core/events.py:151` `RunResumed.value`, `core/ports/store.py:182`)
- [x] An answer that cannot be persisted is refused before the claim (`Input` typing on `RunResumed.value`)
- [ ] External answers do not route by ask identity: `Run.answer(value)` takes a value only (`deck.py:1274`)
- [ ] **conflict** C4: two open asks in one run are refused rather than routed (`core/context.py:386`)
- [ ] Answer buffering across a pause, and therefore `paused_answer_ready`
- [ ] An ask view state (`open`/`answered`/`cancelled`) derived and exposed
- [x] Buffered answers survive recovery, in the case that exists: an answered run's value is in the log, not in memory (`core/events.py:155`)

## injection.md

- [x] The receipt kind exists: `input.appended`, with no producer (`core/events.py:309`)
- [x] A reader already folds it as mid-turn input (`adapters/executors/openai_agents/reconcile.py:130`)
- [x] `ControlVerb` already carries `steer`, declared and not built (`core/events.py:190`, `core/control.py:10`)
- [ ] `input.consumed`
- [ ] `run.inject(value)` on the public surface
- [ ] The ordered durable inbox, and the guarantee that injections neither overwrite nor coalesce
- [ ] Consumption at executor-defined input boundaries
- [ ] Delivery priority `CANCEL > PAUSE > INJECTION > CONTINUE`
- [ ] Refusal of injection against a terminal run
- [ ] Derivable consumed/unconsumed state

Nothing here is built. `agentdeck/authoring/injection.py` is dependency injection for callables and
is unrelated to this document.

## execution-tree.md

- [x] Run identity, not target name, identifies a node, and a repeated call is a distinct run (`design/run-identity.md`)
- [x] The parent edge is durable and carried by exactly one kind (`core/events.py:102` `RunStarted.parent_run_id`, schema `minor=2`, #236)
- [x] A tree is rebuildable from the log, and the tree is what a cost roll-up and a cancel cascade already walk (`runtime/service.py:1030` `delegate`, `:1068` `_rolling_up`)
- [ ] That tree is process-local and exists to bound depth and fan-out, not to be read (`runtime/service.py:1030`)
- [ ] A durable or projected node view: no `run_id`/`parent_run_id`/state/open-asks node type exists
- [ ] Fork/parallel grouping: no causation or invocation identity on the envelope, so a group cannot be rebuilt deterministically
- [ ] Asks exposed at their origin node
- [ ] The whole-tree questions the document lists, none of which anything can answer today
- [x] A parent's state is its own and not an aggregate of its children's (`core/status.py:291`)

## events.md

- [x] The durable append-only log is the authority and status is a fold, never a stored field (`core/status.py:291`, ADR-D5)
- [x] Strict per-run sequence, assigned and appended in one commit (ADR-D11, `core/ports/store.py:76`)
- [x] Envelope carries `v`, `kind`, `seq`, `run_id`, `session_id`, `namespace`, `origin`, `ts`, `payload` (`core/events.py:412`)
- [ ] No `event_id` on the envelope. The contract requires one
- [ ] No `causation_id` or invocation identity on the envelope
- [x] Lifecycle kinds are exactly the kinds that move state, and the two sides cannot drift (`core/status.py:100` `LIFECYCLE_KINDS`)
- [x] Control observability is three distinct facts and all three are produced: `control.requested`, `control.observed`, and the lifecycle kind (`core/control.py:75`, `runtime/service.py:517`)
- [x] Ask, options, accepted answer and refusal are all durable (`core/events.py:179,150,329`)
- [x] Exactly one terminal event per run (`core/events.py:41` `TERMINAL_KINDS`, `core/ports/store.py:123`)
- [x] Observers are downstream of persistence and an observer failure changes nothing (`design/sink-dispatch.md`, `runtime/dispatch.py`)
- [x] An unknown kind degrades rather than raising, which is what makes additive change additive (`core/events.py:412`, schema `minor`)
- [ ] Hash chaining. Optional in this document ("may maintain"), not built, and `projections.md` leans on it heavily enough that its weight should be decided before it is written
- [ ] Branch/fork causation metadata and observer diagnostic kinds, both open per `migration-and-compatibility.md`

## projections.md

- [x] The one projection that exists is the lifecycle fold, computed on demand (`core/status.py:291`, `core/ports/store.py:233`)
- [x] Stores may index by lifecycle kind so finding waiting runs is not a fold of every run (`core/ports/store.py:204`)
- [ ] Nothing else. No projector, no durable cursor, no `last_applied_sequence`/`last_applied_hash`/`projector_version`, no incremental application, no rebuild, no versioning, no freshness reporting, no invalid-projection handling, no run-tree projection

The whole document is unbuilt. It is the largest single piece of work in the set and the one with
no partial credit anywhere.

## observation.md

- [x] `run.status()` (`deck.py:1274`)
- [x] `run.events(from_seq=, follow=)`, and raw events stay available for audit (`deck.py:1274`)
- [x] `deck.runs.list(status=)` as the aggregate over open asks (`deck.py:1508`, `runtime/service.py:890`)
- [x] `run.pending()` returns the open interrupt for one run (`deck.py:1274`, `authoring/interrupts.py:13`)
- [x] Reusable filters over the stream, which is the document's filtering ruling (`views.py:26`)
- [x] An observer is a delivery mechanism and never execution owner (`observers.py`, `design/sink-dispatch.md`)
- [ ] `run.tree()`
- [ ] A snapshot view: no type collects state, terminal outcome, children summary and open asks
- [ ] A live view that emits projection snapshots or diffs
- [ ] Projection freshness metadata, and the read-after-write ruling that depends on it
- [ ] `open_asks()` across a tree rather than `pending()` for one run

## persistence-and-recovery.md

- [x] Lifecycle state, topology, asks and accepted answers all survive restart, because they are log rows (`core/events.py`)
- [x] A suspended run is durable with no task alive, and a handle rehydrated after restart has durable state and no context (`deck.py:1274`)
- [x] Projection rebuild is trivially supported today, because the only projection is recomputed per read
- [x] At most one actor advances a run segment, through leases (`core/ports/lease.py`, `adapters/leases/`)
- [x] Worker loss does not change run identity, and reconciliation is tested (`tests/test_crash_reconciliation.py`, `tests/crash_worker.py`)
- [x] Exactly one terminal outcome survives recovery (`core/ports/store.py:123`)
- [x] Executor recovery capability is stated rather than assumed (`core/ports/executor.py:40`)
- [ ] Pending control does not survive process failure as a durable *log* fact: it lives in the control port, which is a separate store (`core/ports/control.py`). Durable in the sqlite and memory ports, absent from the log the contract calls the authority
- [ ] Injection recovery
- [ ] Buffered answer recovery, and `paused_answer_ready` after restart
- [ ] Rebuild validation against a final sequence or hash

## public-api.md

- [x] `Run.id`, `status()`, `pause()`, `resume()`, `cancel()`, `events()`, `__await__` (`deck.py:1274`)
- [x] `deck.runs.start(...)` and the short `deck.run(...)` path, and starting is not an action on an existing run (`deck.py:1508`)
- [x] `run.can.pause/resume/cancel` as an informational snapshot (`core/status.py:361`)
- [x] `ctx.ask(...)` and `ctx.safepoint()` (`core/context.py:436,199`)
- [x] Per-state rulings for `pause`/`resume`/`answer`/`cancel` (`core/status.py:137` `_LEGALITY`)
- [x] The API does not redefine lifecycle: `Run` delegates every operation back through the deck (`deck.py:1274`, `design/run-identity.md` §3)
- [x] Reading events never advances execution
- [ ] `run.answer(ask_id, value)`: the tree's is `answer(value)` (`deck.py:1274`)
- [ ] `run.inject(value)`
- [ ] `run.tree()`
- [ ] `run.can.inject`
- [x] Error categories are distinguished: no-op, refusal, unsupported, not found (`core/status.py:116` `Verdict`, `errors.py`)

## identity-and-ownership.md

- [x] One canonical durable `run_id`, minted once and never derived (`core/ports/control.py:1`, `design/run-identity.md`)
- [x] Target name is not identity, and repeated calls are distinct runs
- [x] `parent_run_id` is durable and recorded by exactly one kind (`core/events.py:102`)
- [x] Exactly one actor owns advancement, through leases (`adapters/leases/`)
- [x] Observation never becomes execution ownership (`deck.py:1274`, the handle holds no engine)
- [x] Worker replacement does not create a second identity (`tests/test_crash_reconciliation.py`)

This document is fully met. It describes what `design/run-identity.md` already shipped.

## execution-and-adapters.md

- [x] The runtime owns identity, events, transitions and terminality; the executor executes (`core/ports/executor.py:21`, `design/execution-api.md`)
- [x] Executors define their own safe points and the vocabulary is closed (`core/events.py:197`)
- [x] Child runs use the same contract as top-level runs (`core/context.py:336` `invoke`)
- [x] Executors may run children in parallel (`core/context.py:363` `parallel`)
- [x] A replaying executor does not claim to resume in place, and the difference is documented per executor (`core/context.py:199`, `runtime/service.py:469`)
- [ ] Capability is one boolean, `suspendable` (`core/ports/executor.py:40`). The contract names five dimensions: live suspension, continuation after pause, continuation with an answer, injection consumption, recovery after process loss
- [ ] No executor declares injection support, there being no injection
- [ ] Control priority before injection delivery

## errors-and-typing.md

- [x] `RunStateError` (`errors.py:60`)
- [x] `UnsupportedControlError` (`errors.py:72`)
- [x] `NotFoundError` (`errors.py:28`)
- [x] `StoreError`, `DuplicateKeyError`, `SessionBusyError`, `RunSuspendedError` (`errors.py:83,92,49,102`)
- [x] No-op versus refusal is a typed distinction with a reason written for whoever was refused (`core/status.py:116` `Verdict`, `:125` `Precondition`)
- [x] `RunStatus`, `Run`, capability snapshot (`Controls`), lifecycle and event kinds (`core/status.py`, `deck.py`, `core/events.py`)
- [x] An ask type carrying routing identity apart from display payload (`authoring/interrupts.py:13` `InterruptResult`, `core/events.py:179`)
- [x] Terminal typing cannot express two outcomes at once: status is a fold and the last transition wins
- [ ] `RunTree`, `RunTreeNode`
- [ ] Answer/injection validation failure is a bare `ValueError`, not a named category (`runtime/service.py:1202`)
- [ ] Projection-invalid and projection-unavailable errors
- [ ] An injection record type
- [ ] The contract names `Ask`; the tree names `InterruptResult` and `RunInterrupted`. Same rename decision as `ask_id`

## migration-and-compatibility.md

- [x] Every area this file lists as expected migration is confirmed above, and none of it is built beyond what is ticked
- [x] Event schema changes are versioned, and additive change is additive by construction (`core/events.py:68` `CURRENT_VERSION`)
- [x] The rollout order the document recommends matches the dependency order the boxes show: events first, because lifecycle, control, asks, injection and projections all need envelope or kind changes that nothing else can proceed without
- [ ] Each public compatibility decision is not yet marked preserved/deprecated/replaced/removed. Two renames are already owed (`interrupt_id` to `ask_id`, `InterruptResult` to `Ask`) and neither is decided
- [ ] The four conflicts above are withdrawals of shipped behaviour, not additions, and none is listed as such

## verification.md

- [x] Lifecycle table tests, exhaustive over every cell, failing when a state or action is added without a ruling (`tests/core/test_run_lifecycle_tables.py:91,100,107`)
- [x] No ruling is silent (`tests/core/test_run_lifecycle_tables.py:121`)
- [x] The two vocabularies cannot drift apart (`tests/core/test_vocabularies_agree.py`)
- [x] Race tests: pause versus cancel, duplicate cancel, resume versus cancel, answer versus cancel, lost claim re-read (`tests/test_run_control.py`, `tests/test_multiprocess_concurrency.py`, `tests/concurrency_worker.py`)
- [x] Recovery tests: crash and restart preserving suspended state and exactly one terminal outcome (`tests/test_crash_reconciliation.py`, `tests/crash_worker.py`)
- [x] Execution-tree tests for nesting, sub-agents, tools and parallel branches (`tests/test_child_runs.py`, `tests/test_subagents.py`)
- [x] Event ordering and old-reader compatibility (`tests/core/test_events.py`, `test_old_reader_compat.py`)
- [ ] pause versus answer, and two concurrent answers, as named race tests
- [ ] Projection tests: full replay equals incremental application
- [ ] Hash tests
- [ ] Injection tests, all six
- [ ] Multiple simultaneous asks, which C4 currently forbids
- [ ] Buffered-answer and `paused_answer_ready` recovery tests
- [ ] The contract rule itself: nothing fails when production behaviour changes without a matching edit here
