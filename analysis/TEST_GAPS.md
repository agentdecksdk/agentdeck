# Test Gaps

Baseline measured in this audit, `v6.0.3`, on a clean worktree:

```
1876 passed, 188 skipped in 82.31s          # 92% line coverage, 6119 statements
ruff: All checks passed
ty:   All checks passed
import-linter: 12 kept, 0 broken
```

188 skips are almost entirely Postgres (67) and Redis (64) being absent locally.
CI runs both as service containers, so those are covered there, not here.

**Coverage is not the problem.** 92% with contract suites replayed across four
stores, a multiprocess concurrency suite, a crash-reconciliation suite and a
golden event-schema snapshot is a genuinely strong suite. The gaps below are
behavioral, and every one of them sits in a place the percentage looks fine.

## 1. Behavioral matrix

Legend: **Y** covered, **P** partial, **N** not covered, **-** not applicable.

### 1.1 Execution mode x outcome

| behavior | agent (openai_agents) | native `@workflow` | native `@tool` sync (THREAD) | notes |
|---|---|---|---|---|
| success -> `run.completed` | Y | Y | Y | |
| engine raises -> `run.failed` + re-raise | Y | Y | Y | `test_runtime_service.py` |
| cancel while running | Y | Y | P | THREAD body only reaches a safe point after the call returns |
| pause while running | Y | Y | - | `safepoint()` refuses in a sync body |
| resume after pause | Y | Y | - | |
| **resume replays the turn (tool called twice)** | **Y** | - | - | `test_run_control.py:426` asserts `seen == [calendar, calendar]` |
| `ctx.ask` -> `WAITING_ANSWER` -> `answer` | - | Y | - | agent executor raises on `Play.ANSWER` by design |
| answer refused by `options` | - | Y | - | |
| consumer walks away mid-stream | Y | P | P | `GeneratorExit` arm tested at Runtime level, not with a live native body |
| **`deck.run()` return value when the run is cancelled** | Y (raises) | **N** | **N** | returns `None`, indistinguishable from a real `None` |
| **`deck.run()` return value when the run is paused** | Y (raises) | **N** | **N** | same |

### 1.2 Lifecycle transitions

| from -> to | trigger | covered |
|---|---|---|
| (none) -> RUNNING | `claim_start` | Y |
| RUNNING -> COMPLETED / FAILED / CANCELLED | terminal append | Y |
| RUNNING -> PAUSED | gate honors PAUSE | Y |
| RUNNING -> WAITING_ANSWER | `ctx.ask` | Y |
| PAUSED -> RUNNING | `resume_run` claim | Y |
| WAITING_ANSWER -> RUNNING | `resume` claim carrying the value | Y |
| PAUSED -> CANCELLED | cancel found at the resume claim | Y |
| WAITING_ANSWER -> CANCELLED | `_cancel_suspended` | Y |
| WAITING_ANSWER -> (deck closed) | `Deck.aclose()` | **N** |
| terminal -> anything | refused by store | Y |

### 1.3 Cancellation

| case | covered | where |
|---|---|---|
| cancel at a stream safe point | Y | `test_run_control.py` |
| cancel during a tool call | Y | |
| cancel of a suspended run (no loop left) | Y | `_cancel_suspended` |
| cancel races a terminal event | Y | `contract/test_control.py` |
| cancel with no `ControlPort` -> `UnsupportedControlError` | Y | |
| cancel cascades to children, same process | Y | `test_child_runs.py` |
| **cancel cascades to children on another worker** | **N** | `Runtime._tree` is in-memory; behavior is silently "no cascade" |
| `ctx.parallel` gives up its siblings | Y | `test_native_workflow.py` |
| `ctx.parallel` spares a `WAITING_ANSWER` child | Y | |
| **`asyncio.CancelledError` raised by `_record` for an abandoned run** | P | the abandon path is tested; the resulting `Task.cancelled()` state that `Run._result` re-raises is not |

### 1.4 Shutdown

| case | covered |
|---|---|
| `aclose()` on a NEW / BUILT / OPEN / CLOSED deck | Y |
| `aclose()` cancels an in-flight run | Y |
| `aclose()` abandons a run that ignores 2 x 1s cancels | Y |
| `aclose()` closes only what the deck built (store ownership rule) | Y |
| `__aenter__` failure unwinds observers, runtime, store | Y |
| a failing close does not replace the first error | Y |
| **`aclose()` while a native workflow is parked at `ctx.ask`** | **N** |
| **`aclose()` where `Runtime.drain()` raises** (skips sessions, executors, store) | **N** |
| sink `close` timeout / hang / raise | Y |
| `SyncToolWorkers.aclose()` drains a running sync tool | Y |
| `Exposure` teardown order, stdio + http | Y |

### 1.5 Concurrency

| case | covered |
|---|---|
| two turns on one session -> `SessionBusyError` | Y, incl. multiprocess |
| duplicate `(namespace, key)` -> `DuplicateKeyError` | Y, incl. multiprocess |
| two callers race one resume claim | Y |
| killed worker -> lease expiry -> takeover | Y |
| killed worker -> `stale_run_after` -> takeover | Y |
| `seq` contiguity under concurrent appends | Y, all four stores |
| report written from a worker thread lands in order | Y |
| **two `Deck`s in one process** | Y, as a refusal |
| **a second `Deck` after the first closed, with MCP configured** | **N** |

### 1.6 Reporter and context propagation

| case | covered |
|---|---|
| `ctx.reporter` from an async tool | Y |
| `ctx.reporter` from a THREAD tool | Y |
| reports ordered before the terminal event | Y (`#487`) |
| report after the loop is gone -> dropped, logged | Y |
| `context=` reaches a tool, instructions callable, hook, workflow node | Y |
| `context=` is resupplied, not recovered, on resume | Y |
| `context=` type checked at `build()` against `Deck(context=...)` | Y |
| child inherits the parent's `context` by reference | Y |
| a handle from `runs.get()` has no context | Y |

### 1.7 Store contract

Contract suite runs the same cases against memory, sqlite, redis and postgres:
append, read_run, read_session, claim_start, claim_resume, list_runs,
find_by_key, run_status, negative limits, stale takeover, dead-lease takeover.
This is the best-tested surface in the repo.

## 2. Gaps ranked

| # | gap | why it matters | cheapest test |
|---|---|---|---|
| 1 | `deck.run()` on a workflow returns `None` for cancelled or paused | silent wrong answer on the primary API | start a slow workflow, cancel it, assert on the return |
| 2 | `Deck.aclose()` with a parked `ctx.ask` workflow | run left `WAITING_ANSWER` forever, session wedged, `pending()` lists a phantom, `answer()` destroys it | the probe in `analysis/CODEBASE_AUDIT.md` AD-04, as a test |
| 3 | second `Deck` in one process, MCP configured | `MCPLifecycle._servers` is never cleared; deck 2 reuses cleaned-up server objects and deck 1's config | open, close, open again with a different `.mcp.json`, assert the servers |
| 4 | cross-process cancel cascade | documented behavior that does not happen | extend `test_multiprocess_concurrency.py` |
| 5 | `aclose()` where `drain()` raises | sessions, executors and store all leak | inject a raising sink `close` |
| 6 | `Run.status()` assertion on a child whose claim failed | `AssertionError` instead of the real error; vanishes under `python -O` | make the store raise inside `_invoke`'s task |
| 7 | README's headline example | it raises `ConfigError`; nothing executes README fences | add `README.md` to `test_docs_examples.py`'s stage-2 harness |

## 3. Tests that assert implementation rather than behavior

Rare, and mostly justified. Three worth naming:

| test | what it pins | comment |
|---|---|---|
| `test_the_mcp_source_is_a_tool_source_port` | `isinstance(MCPToolSource(), ToolSourcePort)` | the only thing holding a port with zero production callers alive |
| `tests/core/test_golden_json.py` | byte-level event snapshots | correct for a wire schema, and gated behind `make golden` |
| `test_run_config_parity.py` | field-by-field equality of two `RunConfig`s | pins `HeadlessRunner`, itself reachable only from `Agent.run()` |

One whole module is skipped: `tests/core/test_old_reader_compat.py` (5 tests)
carries a module-level `pytest.mark.skip`, with a correct reason (no released
reader can parse the current envelope, so there is nothing to measure). The
consequence is worth stating anyway: the envelope-level forward-compatibility
guarantee currently rests on `test_old_reader_block_compat.py` and the golden
snapshots alone, and the skip has no expiry condition anyone will trip over.

## 4. What the suite protects well

Say this plainly, because the gaps above are not the whole picture:

- Event ordering and `seq` density, across four stores, including under
  concurrent writers and a killed process.
- The session-claim invariant (one turn per session) across processes.
- The whole `core/status.py` lifecycle table, keyed exhaustively so a missing
  cell fails at import.
- Forward and backward wire compatibility (`test_old_reader_compat.py`,
  `test_old_reader_block_compat.py`, golden snapshots replayed in a second
  process).
- Sink isolation: slow, hanging, raising and cancellation-swallowing sinks all
  have named tests.
- Documentation: fences are parsed, and opted-in fences are executed as real
  subprocesses against a scripted model server. This is unusually good.
