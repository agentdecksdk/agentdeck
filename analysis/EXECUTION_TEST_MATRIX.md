# Minimum behavioral test matrix for AgentDeck's execution modes

Base: `v6.0.3` (`b9a7f76`). Companion to `analysis/EXECUTION_MODEL_AUDIT.md`.

What this file is: the smallest set of behavioral tests that would have caught every finding in the
audit, and that would keep catching them. It is not a coverage target. Two of the audit's Critical
findings live in code that is already covered by passing tests.

---

## 1. The axes that actually vary

The brief proposes `mode × outcome × concern`. Against this implementation, "mode" is not one axis.
There are two independent ones, and conflating them is what hid five of the eight top findings.

| axis | values | why it is separate |
|---|---|---|
| **A. who plays the run** | agent turn (`openai-agents`) · native body | decides whether a `Gate` checkpoint is reached automatically |
| **B. what the body is** | `async def` with `ctx.safepoint()` · `async def` **without** · `def` (thread) | decides whether a checkpoint exists at all, and the cancel/completion tie-break |
| **C. where the body is called from** | as a run · as a tool inside an agent turn · `instructions=` · `hooks=` | decides which `ToolCtx` is built, so which `safepoint()` behavior applies |
| **D. process topology** | one process · two workers sharing a store | decides whether the cascade, the roll-up and the parked body survive |

The existing suite varies A and D well, varies B only in its instrumented form, and does not vary C
for control at all.

**The one rule this matrix enforces: no cancellation or shutdown test may use a body that calls
`ctx.safepoint()`, unless a paired test uses one that does not.** Every cancellation test in the
suite today instruments the body, so the suite measures the mechanism and never the contract.

---

## 2. Current coverage

Legend: ✅ protected · ⚠️ partial, see note · ❌ no test · n/a not applicable.

| Row (A × B × C) | Success | Exception | Cancel | Shutdown | Reporter | Context |
|---|---|---|---|---|---|---|
| agent turn | ✅ | ✅ | ✅ `test_run_control.py:255` | ✅ | ✅ | ✅ |
| native `async` **with** safepoint, as a run | ✅ | ✅ | ✅ `test_native_workflow.py:327` | ✅ | ✅ | ✅ |
| native `async` **without** safepoint, as a run | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| native `def` (thread), as a run | ✅ | ✅ | ✅ `test_sync_tool_workers.py:174` | ⚠️ a | ✅ | ✅ |
| `async` tool inside an agent turn | ✅ | ✅ | ⚠️ b | ✅ | ✅ | ✅ |
| `async` tool inside an agent turn, calls `safepoint()` | n/a | n/a | ❌ | n/a | n/a | n/a |
| `def` tool inside an agent turn | ✅ | ✅ | ⚠️ b | ✅ | ✅ | ✅ |
| `def` in `instructions=` | ✅ | ✅ | ❌ | ❌ | ⚠️ c | ✅ |
| `def` in `hooks=` | ✅ | ✅ | ❌ | ❌ | ⚠️ c | ✅ |
| child via `ctx.invoke`, **with** safepoint | ✅ | ✅ | ✅ `test_child_runs.py:212` | ✅ | ✅ | ✅ |
| child via `ctx.invoke`, **without** safepoint | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| two workers, one store, one run | ✅ | ✅ | ✅ `test_multiprocess_concurrency.py:272` | ✅ crash | n/a | n/a |
| two workers, one store, parent + child | n/a | n/a | ❌ | n/a | n/a | n/a |

Notes:

- **a** `test_sync_tool_workers.py:258` proves `Deck.aclose` does not deadlock the loop, and releases
  the worker 0.1s into the close. Nothing tests a worker that never releases (EXEC-04).
- **b** the turn's own checkpoint honors the cancel; nothing tests a cancel that the *tool* observes.
- **c** reports from these bodies work, but nothing asserts where they run.

Timeout has no column because there is no run-level timeout to test; see §5.

---

## 3. Required tests

Twenty-two tests. Each names the finding it would have caught. Ordered by the priority in the
audit's §15.

### 3.1 Cancellation contract (the blind spot)

| # | test | asserts | catches |
|---|---|---|---|
| C1 | cancel a native `async def` workflow that never calls `safepoint()`, then let it finish | either the run ends `CANCELLED`, or `run.can.cancel` was `False` and `Run.cancel()` said so. Never: `run.completed` after a successful `cancel()` | EXEC-02 |
| C2 | the same, for an `async def` `@tool` reached by `ctx.invoke` | same | EXEC-02 |
| C3 | cancel a run whose `@tool` (inside an agent turn) calls `await ctx.safepoint()` | the run ends `CANCELLED`; `tool.call.completed.error` does **not** carry a control exception; the model is not handed a tool error | EXEC-01 |
| C4 | the same, for `pause` | the run ends `PAUSED`, or the pause stays pending. Never: swallowed as a tool error | EXEC-01 |
| C5 | `ctx.parallel(failing, sibling_without_safepoint)` | the sibling does not run to completion after the parent raises, or the docstring's all-or-nothing promise is scoped and the test asserts the scoped version | EXEC-09 |
| C6 | cancel a run that ends within one `CONTROL_POLL_INTERVAL` of the last checkpoint | `control.requested` is in the log even when the ruling is a no-op, and the control port holds no row for the run once it is terminal | EXEC-15 |
| C7 | cancel twice, then complete | exactly one terminal event; the second cancel is a no-op | already implied by `_refuse_if_sealed`, not asserted at the `Deck` level |
| C8 | cancel a native run, then assert `NativeExecutor._parked` is empty and the body task is done | no orphan | EXEC-05 |

### 3.2 Mode parity

The parity tests are the highest-value new shape: one body written twice, `def` and `async def`,
asserted to produce the same observable outcome.

| # | test | asserts | catches |
|---|---|---|---|
| P1 | cancel arrives while the body is working; the body then returns a value | the same terminal state for `def` and `async def` | EXEC-10 |
| P2 | cancel arrives while the body is working; the body then raises | the same terminal state and the same recorded `error_code` for both | EXEC-10 |
| P3 | `run.can.pause` on a `@tool` run of each kind | matches what `run.pause()` actually achieves | EXEC-03 |
| P4 | `ctx.safepoint()` in each of the four `ToolCtx` construction sites | one documented behavior per site, and the doc table matches | EXEC-01, EXEC-12 |
| P5 | a blocking `def` in `hooks=` while a second run streams on the same loop | the second run is not stalled, or the docs state that it is | EXEC-12 |

### 3.3 Front-door parity

| # | test | asserts | catches |
|---|---|---|---|
| F1 | cancel the caller of `deck.run(...)` and of `deck.stream(...)` mid-run | the same recorded outcome for both; and it matches what actually happened to the body | EXEC-08 |
| F2 | close a `deck.stream(...)` generator mid-run | the run's own outcome is unchanged (`stream`'s documented rule) | already covered indirectly, not asserted at the front door |

### 3.4 Shutdown

| # | test | asserts | catches |
|---|---|---|---|
| S1 | `deck.aclose()` behind a `def` `@tool` that never returns | returns within a stated bound, logs what it abandoned | EXEC-04 |
| S2 | `deck.aclose()` then assert every registry is empty | `Deck._executions`, `NativeExecutor._parked`, `Runtime._tree` | EXEC-05, EXEC-06 |
| S3 | `deck.aclose()` with `AGENTDECK_CONTROL=sqlite://`, then assert both port connections are closed | file descriptors released | EXEC-13 |
| S4 | two sequential `Deck`s in one process, each with `mcp=` | the second connects successfully | EXEC-18 |
| S5 | `deck.aclose()` on a `Deck` built but never opened | the worker pool is shut down | EXEC-14 |

### 3.5 Resource bounds

| # | test | asserts | catches |
|---|---|---|---|
| R1 | N `SessionBusyError` refusals on one session | `len(Runtime._tree)` does not grow with N | EXEC-06 |
| R2 | N abandoned runs (consumer walks away) | `len(NativeExecutor._parked)` does not grow with N | EXEC-05 |
| R3 | N unhonored pause signals against completed runs | the control port holds no rows for terminal runs | EXEC-03, EXEC-15 |

### 3.6 State machine

| # | test | asserts | catches |
|---|---|---|---|
| M1 | append `run.cancelled` after `run.failed`, per store, in the contract suite | refused, or `STATES[FAILED].terminal` is `False` and the diagram says so | EXEC-11 |
| M2 | a takeover writes `run.failed`, then the original worker writes `run.completed` | one of the two is refused; the session is not owned by two turns | EXEC-11 |

### 3.7 Cross-process

| # | test | asserts | catches |
|---|---|---|---|
| X1 | worker A runs a parent with two children; worker B cancels the parent | the children stop, or the docstring scopes the cascade and the test asserts the scoped version | EXEC-07 |
| X2 | worker A runs a parent; worker B reads `run.completed` usage | the roll-up is either correct or documented as live-only | EXEC-07 (roll-up half) |

### 3.8 Exceptions

| # | test | asserts | catches |
|---|---|---|---|
| E1 | `await` a run that ended `CANCELLED` | raises a typed `AgentdeckError`, not a bare `RuntimeError`; carries the run id and the reason | EXEC-16 |
| E2 | `await` a handle recovered by `Runs.get` on a run that failed in another process | a typed error, with whatever the log can carry | EXEC-16 |

---

## 4. Target matrix, once the tests above exist

| Row | Success | Exception | Cancel | Shutdown | Reporter | Context | Resource bound |
|---|---|---|---|---|---|---|---|
| agent turn | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | S2 |
| native `async` **with** safepoint | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | S2, R2 |
| native `async` **without** safepoint | ✅ | ✅ | **C1, C2** | **S2** | ✅ | ✅ | **R2** |
| native `def` (thread) | ✅ | ✅ | ✅ + **P1, P2** | **S1** | ✅ | ✅ | **R2** |
| tool inside an agent turn, `safepoint()` | n/a | n/a | **C3, C4** | n/a | n/a | **P4** | n/a |
| `instructions=` / `hooks=` | ✅ | ✅ | **P4** | **P5** | ✅ | ✅ | n/a |
| child via `ctx.invoke`, no safepoint | ✅ | ✅ | **C5** | **S2** | ✅ | ✅ | **R1** |
| two workers, parent + child | n/a | n/a | **X1** | ✅ | n/a | n/a | **X2** |

---

## 5. Deliberately not in the matrix

| | why |
|---|---|
| a process execution mode | none exists. `NativeExecution` has two members, `ASYNC` and `THREAD`, both inferred. Nothing in `agentdeck/` creates a process. |
| serialization of a target, a return value, an exception or a context | no serialization boundary exists inside a run. `ctx.data` never crosses one by design. |
| pickling, IPC, zombie processes, worker reuse | same. When a sandbox backend exists, these become the matrix's second half; see the audit's §15 sandbox-boundary table. |
| Windows behavior | no process, no signal handling and no path-dependent execution in the package. The sqlite stores' WAL caveat is documented and is a filesystem concern, not an execution one. |
| a run-level timeout | there is none, deliberately. `RunContext`'s docstring states the rule: a field comes back with the thing that enforces it. Testing an absent feature would freeze the absence. |
| forced termination | no mechanism exists in any mode, and none can exist for a Python thread. §6 of the audit records this as essential complexity. |
| coverage percentage | two of the audit's Critical findings are in lines the current suite executes and passes. |

---

## 6. The one thing to build first

If only one test from this file ships, make it **C1**.

```
a native async @workflow with no ctx.safepoint(), cancelled mid-run,
must not end run.completed after Run.cancel() returned successfully
```

It is four lines of setup, it fails today, and it is the default shape of the first workflow every
user writes.
