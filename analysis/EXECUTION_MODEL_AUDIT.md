# AgentDeck execution model audit

Base: `v6.0.3` (`b9a7f76`). Branch `audit/execution-model-v6.0.3`, worktree `wrts/audit-exec-603`.
Read-only. No production code was changed. Gate at baseline: `make check` green (ruff, ty,
import-linter 13 contracts, 1876 passed / 188 skipped).

Every behavioral claim below was either traced through the complete code path or reproduced with a
probe against this checkout. Probe results are quoted verbatim where they carry the finding.

---

## 1. Executive summary

**Answer to the core question: AgentDeck has one coherent *event and state* model and several
subtly different *control* models hidden behind one API.**

The parts that are genuinely one model are strong. There is exactly one `Run`, one `RunContext`,
one `Reporter` class, one `Event` stream, one `Executor` port, one state fold (`core/status.py`),
and one place a run is orchestrated (`Runtime._play`). Persist-before-yield, store-assigned `seq`,
conditional-append claims and the four lifecycle tables are unusually disciplined, and they hold
across processes. Nothing in this audit disputes that layer.

The break is at the control seam. `Run.cancel()` and `Run.pause()` are *requests written to a
port*, and whether a request ever becomes an effect depends on something the API never exposes:
whether the code being executed reaches a `Gate` checkpoint. Three of the four execution paths
answer differently, and two of them answer "never" for the code a user writes by default.

| what is executing | who reaches a checkpoint | `run.cancel()` on a live run |
|---|---|---|
| an agent turn (`openai-agents`) | the executor, after every SDK stream event | honored, `run.cancelled` |
| a native `async` body | only where the author wrote `await ctx.safepoint()` | **silent no-op**, run completes, signal stranded |
| a native `sync` body | the executor, once, after the body returns | honored late, after the side effects |
| a `@tool` inside an agent turn calling `ctx.safepoint()` | the tool itself | **destroyed**, becomes a model-visible tool error |

`run.can.pause` returns `True` in all four rows, because it is derived from
`Executor.suspendable`, a `ClassVar` on the executor, while the capability it describes belongs to
the body. `run.pause()` then succeeds, records nothing, and leaves a row in the control port that
nothing will ever read.

The second theme is ownership at the edges of an otherwise well-owned tree. Every asyncio task in
the package has a named owner except two, but two owned resources are never released
(`ControlPort`, `LeasePort`), one registry grows without bound on a routine error path
(`Runtime._tree` on `SessionBusyError`), one registry never releases entries at all
(`NativeExecutor._parked` for an abandoned run), and the one shutdown wait that is not
deadline-bounded (`SyncToolWorkers.aclose`) is the one a user's own code can hold open forever.

**There is no process execution mode.** The audit brief's `@tool(execution="process")` does not
exist in this codebase and never has. `NativeExecution` has two members, `ASYNC` and `THREAD`, it
is inferred from `inspect.iscoroutinefunction` at decoration time, and it is not spellable by a
user. Nothing in `agentdeck/` creates a process, a process pool, or a pickle boundary. What
AgentDeck *does* support cross-process is several worker processes sharing one durable store; that
is audited in §12 as a separate subject.

### Verdict in one line

One execution model with two backends and a leaky cooperative-control seam, not several models,
but the seam leaks enough execution detail into the user's mental model that "change only the
execution mode and the contract holds" is currently false.

---

## 2. Actual execution architecture

### 2.1 Real execution map

```
caller
  │  deck.run(name, input)      deck.stream(...)      deck.runs.start(...)      ctx.invoke(target)
  ▼
Deck._start  /  Deck._invoke                                  deck.py:1023 / deck.py:1053
  │  mints nothing (Runtime mints run_id) except for _invoke, which mints it early
  │  awaits the first event (run.started) only on _start
  ▼
asyncio.create_task(_drain(runtime.run(...)))     ← THE run-owning task
  │  registered in Deck._executions[run_id] = (namespace, task)
  ▼
Runtime.run                                                   runtime/service.py:189
  ├── _resolve(name) → (InvocableSpec, Executor)
  ├── _bind(ctx, spec) → attaches Gate + Reporter to a frozen RunContext
  │       Gate(control_port, run_id)                          core/control.py:110
  │       Reporter(fire)  where fire = deque append + run_coroutine_threadsafe(loop)
  ├── delegate(run_id, parent_run_id, ...) → Runtime._tree     (depth ≤ 3, fanout ≤ 8)
  ├── _history(ctx) → whole session log (or whole run log)
  └── _claim_session → store.claim_start  ← conditional append, cross-process mutual exclusion
        │
        ▼
Runtime._play(opening, executor.execute(...), ...)            runtime/service.py:571
  │  holds the lease (_holding), yields opening, then every payload
  │  per payload: settle reports if terminal → _record (append → fan out) → yield
  │  four exits: terminal payload · suspension · GeneratorExit · CancelledError · Exception
  ▼
Executor.execute  ── one of two ─────────────────────────────────────────────────────────────┐
  │                                                                                          │
  ├─ OpenAIAgentsExecutor                     adapters/executors/openai_agents/executor.py  │
  │    Runner.run_streamed(agent, msg, context=ctx, session=..., run_config=...)             │
  │    detached SDK run loop; per stream event: translate → yield → await ctx.gate.checkpoint()
  │    tools: FunctionTool built by authoring/tools.py:compile_tool                          │
  │       ├─ async body → awaited inline on the run's loop                                   │
  │       └─ sync  body → SyncToolWorkers.submit (deck-owned ThreadPoolExecutor)             │
  │    ToolCtx(run, _loop=None|loop)   ← NO _channel: safepoint() raises out of the tool     │
  │                                                                                          │
  └─ NativeExecutor                            adapters/executors/native/executor.py         │
       asyncio.create_task(self._play(definition, input, ctx, channel))  ← THE body task     │
       registered in NativeExecutor._parked[run_id]                                          │
       _Channel: unbounded asyncio.Queue out, one asyncio.Future back for the answer         │
         ├─ NativeExecution.ASYNC  → result = await definition.call(**args)   (no checkpoint)│
         └─ NativeExecution.THREAD → await workers.submit(call, **args)                      │
                                     then ctx.gate.checkpoint_cancel_only("tool_dispatch")   │
       ToolCtx/WorkflowCtx(run, channel, _loop=...)  ← HAS _channel: safepoint() can park    │
                                                                                             │
result / exception ──────────────────────────────────────────────────────────────────────────┘
  │  native: body task's return → RunCompleted on the channel; its exception → re-raised by
  │          `await body.task` in execute(), caught by _play's `except Exception`
  │  agents: RunResultStreaming.final_output → RunCompleted
  ▼
Runtime._record → store.append (stamps seq + ts, refuses a sealed log) → _fan_out
  │                                                                        │
  │                                                                        ▼
  │                                                    SinkDispatch per Observer
  │                                                      bounded Queue(256) + one consumer task
  │                                                      drop-oldest, breaker, 5s emit deadline
  ▼
run state = the fold of the log (core/status.status_of), no status table anywhere
  ▼
caller
   deck.run      → await task (real exception) → read log → TurnResult / workflow value
   deck.stream   → Deck._events tails the STORE, then await task
   Run.__await__ → poll status; if this process owns the task, await it for the real exception
```

### 2.2 What is created where, and who owns it

| primitive | created at | owner / registry | released by |
|---|---|---|---|
| run-owning task (`_drain`) | `deck.py:1048` (`_start`), `deck.py:1080` (`_invoke`) | `Deck._executions[run_id]` | `_execution_done` on settle; `Deck.aclose` cancels the rest |
| native body task | `native/executor.py:176` | `NativeExecutor._parked[run_id]` | popped only when the body ends *and* `execute()` is still reading; otherwise **never** (EXEC-05) |
| sink consumer task | `dispatch.py:304` | `SinkDispatch._consumer` | `SinkDispatch.close`, abandoned after `REAP_TIMEOUT` |
| queue-join task | `dispatch.py:192` | local to `flush` | cancelled and awaited |
| lease renewer task | `service.py:690` | local to `_holding` | cancelled, **never awaited** (EXEC-19) |
| shielded cancel-write | `service.py:901` | none after the shield is cancelled | nothing (EXEC-19) |
| stdio binding task | `exposure.py:50` | `Exposure._lifecycle` | cancelled and awaited |
| `SyncToolWorkers` pool | `Deck.__init__` (`deck.py:587`) | `Deck` constructs, `NativeExecutor` closes | `NativeExecutor.aclose` only (EXEC-14) |
| stdin pump thread | `terminal/binding.py:56` | none, daemon by design | interpreter exit |
| `asyncio.to_thread` threads (sqlite store/control/lease, langfuse flush) | per call | interpreter default executor | loop shutdown |
| `EventStorePort` | `resolve_event_store()` in `Deck.__aenter__` | `Deck._owns_store` | `Deck.aclose` if owned |
| `ControlPort`, `LeasePort` | `build_runtime` → `resolve_*` | `Runtime`, unnamed | **nobody** (EXEC-13) |
| `ExecutionStore` (SDK sessions) | `Deck._ensure_sessions` | `Deck._sessions` | `Deck.aclose` |
| MCP servers | `MCPLifecycle` class attributes | process-global | `MCPLifecycle.shutdown`, partial (EXEC-18) |
| `Runtime._tree` entry | `Runtime.delegate` | `Runtime._tree` | `_rolling_up` on a terminal `_record`; **never** on a refused claim (EXEC-06) |
| `Runtime._abandoned` entry | `close_cancelled` | `Runtime._abandoned` | never, bounded by deck lifetime |

### 2.3 Where each cross-cutting concern travels

| concern | mechanism | crosses a thread | crosses a process |
|---|---|---|---|
| `ctx.data` | field on the frozen `RunContext`, by reference | yes, same object, no copy, no lock | no, resupplied by the caller on resume/answer |
| `ctx.gate` | `Gate` bound in `Runtime._bind`, reads `ControlPort` | yes (`checkpoint_cancel_only` is awaited on the loop) | via `sqlite://` control port only |
| `ctx.reporter` | one `Reporter`, sync API, `write` → deque + `run_coroutine_threadsafe(loop)` | yes, by design | no, loop-bound |
| result | native: `_Channel` queue; agents: `RunResultStreaming` | yes (`asyncio.wrap_future`) | no |
| exception | native: `await body.task` re-raise; agents: SDK raise | yes (`concurrent.futures` re-raise) | no, degrades to `RunFailed.message` = type name |
| cancel signal | `ControlPort` row, read at a `Gate` checkpoint | yes | via `sqlite://` control port only |
| cancel cascade | `Runtime._tree` walk in `signal()` | n/a | **no** (EXEC-07) |
| shutdown | `Deck.aclose` → task cancels → `Runtime.drain` → `Executor.aclose` → store close | yes | no |
| run state | the event log, folded | n/a | yes, this is the durable seam |

---

## 3. Execution-mode comparison matrix

Four columns, because those are the four real paths. "Sync" is a `def` body; "Async" is an
`async def` body. There is no process column: no process backend exists.

| Behavior | Agent turn (`openai-agents`) | Native async body | Native sync body | Tool inside an agent turn |
|---|---|---|---|---|
| Return result | `RunResultStreaming.final_output` → `run.completed` | body return → `run.completed` | body return → `run.completed` | returned to the SDK, becomes a tool result |
| Exception propagation | SDK exception → `run.failed(engine_error)` + raised to caller | body exception → `run.failed` + raised to caller | same, **unless a cancel is pending, then discarded** | caught by `failure_error_function`, becomes a tool-error string, run continues |
| Cancellation | honored at the next stream item, automatic | **only where the author wrote `ctx.safepoint()`** | honored once, after the body returns | **destroyed**: consumed, then swallowed as a tool error |
| Forced termination | none. no mechanism kills a thread or a running SDK loop | none | none | none |
| Graceful shutdown | task cancelled, `run.cancelled` written under `shield` | task cancelled, `run.cancelled` written, **body keeps running** | as async, plus an **unbounded** pool drain | as its host turn |
| Context availability | `ctx` is the SDK context object | injected, by reference | injected, by reference, same object across threads | injected, by reference |
| Reporter availability | yes | yes | yes, cross-thread via `run_coroutine_threadsafe` | yes |
| Cleanup | `result.cancel()` on every exit; `_launch` `GeneratorExit` path | `channel.close()` in `finally` | same | none needed |
| Timeout behavior | `max_turns` only. no wall-clock deadline anywhere | none | none | none |
| Pause/resume | pause replays the turn from the log on resume | pause **parks the live coroutine**, resume continues the next line | `checkpoint_cancel_only` **ignores pause**; `ctx.safepoint()` raises `ConfigError` at call time | `ctx.safepoint()` raises `RunPausedError` out of the tool, swallowed as a tool error |
| Resource ownership | SDK run loop is detached, cancelled by the executor | body task in `_parked`, leaks when abandoned | body thread in the deck pool, never interruptible | the host turn's |
| `run.can.pause` reports | `True` (correct) | `True` (**wrong unless the body checkpoints**) | `True` (**always wrong**) | n/a |

Classification of the differences:

| difference | kind |
|---|---|
| a thread cannot be interrupted, so a sync body always runs to its end | **cannot technically be identical** |
| a paused agent turn replays, a paused native workflow parks | **cannot technically be identical**, and is documented per executor |
| a native async body needs an explicit `ctx.safepoint()` while an agent turn does not | **unnecessarily different**: the executor could offer a checkpoint at the same boundaries the agent executor does, or the API could stop claiming the capability |
| `checkpoint_cancel_only` honors cancel but drops pause | **accidentally inconsistent**: nothing tells the caller, `run.pause()` still succeeds |
| a sync body's own exception is discarded when a cancel is pending, an async body's is not | **accidentally inconsistent** |
| `deck.run` records `run.cancelled` for a caller cancellation, `deck.stream` records `run.completed` | **accidentally inconsistent** |
| `ctx.safepoint()` parks under the native executor and raises under the agent executor | **unnecessarily different**: one call, two outcomes, chosen by which executor happens to be playing |

---

## 4. Top findings

| id | severity | title |
|---|---|---|
| EXEC-01 | Critical | `ctx.safepoint()` inside an agent-turn tool consumes the signal and turns it into a model-visible tool error |
| EXEC-02 | Critical | `Run.cancel()` means three different things, and for the default native body it means nothing |
| EXEC-03 | High | `run.can.pause` describes the executor, not the body, so it is wrong for every `@tool` run |
| EXEC-04 | High | `Deck.aclose()` is unbounded when a sync `@tool` body does not return |
| EXEC-05 | High | An abandoned native run leaks its body task, its channel and its `_parked` entry, and the body runs on past `run.cancelled` |
| EXEC-06 | High | `Runtime._tree` grows without bound: a refused opening claim is never removed from the delegation tree |
| EXEC-07 | High | The cancel cascade is in-process only, and nothing says so |
| EXEC-08 | High | `deck.run()` and `deck.stream()` record opposite outcomes for the same caller cancellation |

---

## 5. Full findings

### EXEC-01 -- `ctx.safepoint()` inside an agent-turn tool consumes the signal and turns it into a model-visible tool error

**Severity:** Critical
**Confidence:** High
**Modes affected:** Async, Sync (any `@tool` played inside an `openai-agents` turn)
**Category:** Cancellation

#### Evidence

Path, in order:

1. `agentdeck/authoring/tools.py:158` builds the tool's context as
   `ToolCtx(run, _loop=None if awaits else asyncio.get_running_loop())`. There is **no
   `_channel`**: the SDK's tool call is not something the native executor can park.
2. `agentdeck/core/context.py:227 ToolCtx._safepoint` calls `await self._run.gate.checkpoint()`.
3. `agentdeck/core/control.py:159` consumes the pending intent from the `ControlPort` **before**
   raising: `if ruling.consume: await self._control.consume(self._id, pending.verb)`.
4. `agentdeck/core/context.py:231`: `if self._channel is None: raise`. The `ControlSignalled`
   leaves the tool body.
5. `agentdeck/authoring/tools.py:76-77` compiled the tool with
   `failure_error_function=_tool_failure`. The Agents SDK catches every `Exception` a function
   tool raises and calls that function, so `RunCancelledError` (an `Exception` subclass, by
   design: `core/control.py:55`) becomes a tool-error string handed to the model.
6. The turn continues, the model recovers, and `OpenAIAgentsExecutor` yields `run.completed`.

#### Current behavior

Reproduced end to end (scripted model, one tool call, cancel issued while the tool is awaiting,
released after 0.5s so the gate's poll-reuse window has elapsed):

```
tool raised: ['RunCancelledError']
final status: completed
kinds: ['run.started', 'usage.reported', 'tool.call.started', 'tool.call.completed',
        'text.delta', 'usage.reported', 'message.completed', 'run.completed']
```

The operator's cancel is gone in three ways at once: consumed from the control port, absent from
the event log (no `control.requested`, no `control.observed`, no `run.cancelled`), and leaked to
the model as a tool failure it was never meant to see.

#### Expected behavior

`docs-site/content/build-your-deck/context.mdx:76` lists `await ctx.safepoint()` as a `ToolCtx`
capability, "yes, async body only", with no executor qualifier. `core/status.py:191 Action`'s own
invariant is stated as "no read of the control port ends in silence". This read ends in silence
plus a corrupted turn.

#### Problem

`ToolCtx._safepoint`'s `raise` is correct in isolation and is unit-tested
(`tests/core/test_context.py:79`). It is wrong in the only place it actually happens, because the
caller of that raise is the Agents SDK's tool-error handler. The seam that decides the outcome
(`_channel is None`) is set by which executor compiled the tool, which the author of the tool
cannot see.

#### Real scenario

An operator clicks Stop on a support agent that is 20 seconds into a `@tool` doing a slow vendor
call. The tool does the right thing and calls `await ctx.safepoint()`. The model is told
"An error occurred while running the tool", apologises, retries the same tool, and the run
completes normally. The Stop button did nothing, the audit log has no record that anyone pressed
it, and the run is billed for the retry.

#### Impact

Correctness (a control operation is lost), safety (Stop does not stop), DX (the documented API
does the opposite of what it says), and data (an internal control exception reaches the prompt).

#### Recommended direction

`ToolCtx._safepoint` with no `_channel` should not raise into user code that is running inside
somebody else's error handler. The smallest shape is for the compiled bridge to catch
`ControlSignalled` from the tool body and re-record the intent on the port (or re-raise it as a
`BaseException` the SDK does not catch), so the host executor's own next checkpoint honors it.
Do not implement here.

**Scope:** Multi-module (`authoring/tools.py` + `core/context.py`).

---

### EXEC-02 -- `Run.cancel()` means three different things, and for the default native body it means nothing

**Severity:** Critical
**Confidence:** High
**Modes affected:** All
**Category:** Cancellation

#### Evidence

`Runtime.signal` (`service.py:432`) writes a row to the `ControlPort` and returns `True`. The row
becomes an effect only where something calls `Gate.checkpoint`. The call sites are:

| call site | file:line | reached by |
|---|---|---|
| after every SDK stream event | `openai_agents/executor.py:135` | every agent turn, automatically |
| after a THREAD body returns or raises | `native/executor.py:208,210` | every sync `@tool`, automatically, but only once and only after the work |
| `ctx.safepoint()` | `core/context.py:229` | only where the author wrote it |

There is **no checkpoint at all** around `native/executor.py:212`
(`result = await definition.call(**arguments)`), the ASYNC branch.

#### Current behavior

One probe, three targets, identical timing (cancel issued while the body is blocked, then
released):

```
THREAD @tool                       kinds=['run.started','control.requested','control.observed','run.cancelled']
ASYNC  @tool                       kinds=['run.started','run.completed']
ASYNC  @workflow (no safepoint)    kinds=['run.started','run.completed']
```

`Run.cancel()` returned normally in all three cases. In the two that completed, the cancel row is
left in the control port permanently (measured: `ControlSignal(verb=CANCEL, reason='...')` still
pending after the run reached `completed`).

#### Expected behavior

`Run.cancel`'s own docstring (`deck.py:1464`): "Ask the run to stop for good. A live run stops at
its next safe point." `docs-site/content/runs-and-control/lifecycle-and-control.mdx:70`: "The run
records the signal, then acts on it when it next reaches a safe point: between stream items,
before dispatching a tool, or at a node boundary."

Both sentences promise that a safe point exists. For a native async body, none does.

#### Problem

Cooperative cancellation is the right mechanism. The defect is that the capability is advertised
unconditionally while the obligation (write `ctx.safepoint()`) is neither enforced, checked, nor
documented anywhere a workflow author reads. `docs-site/content/runs-and-control/pause-resume.mdx`,
which the lifecycle page links as "safe points in more detail", is 18 lines long and contains two
code lines.

#### Real scenario

A `@workflow` fans out to three vendor APIs with `ctx.parallel`, taking 90 seconds. An operator
cancels it. `run.cancel()` succeeds, `run.status()` still reports `running`, the workflow completes
90 seconds later and writes its results. Nothing in the log says a cancel was ever requested.

#### Impact

Correctness, safety, cost (a cancelled run keeps spending), and the whole trust model of the
control plane.

#### Recommended direction

Three options, in order of preference:

1. Give the native executor its own automatic checkpoint boundary the way the agent executor has
   one, so a body that awaits anything reaches one. This makes the contract uniform and the
   `ctx.safepoint()` call an optimisation rather than a requirement.
2. Failing that, make the claim conditional: derive `run.can.cancel`/`run.can.pause` from
   something that knows whether the body checkpoints, and make `Run.cancel()` say when it cannot
   be honored.
3. At minimum, document the obligation at the `@workflow`/`@tool` decorator and in
   `pause-resume.mdx`, and prune stranded signals when a run reaches a terminal state.

Do not implement here.

**Scope:** Architectural.

---

### EXEC-03 -- `run.can.pause` describes the executor, not the body

**Severity:** High
**Confidence:** High
**Modes affected:** Async, Sync (native executor)
**Category:** API / State

#### Evidence

- `core/status.py:369 can_of(status, *, suspendable)` = `suspendable and PRECONDITIONS[...] is LEGAL`.
- `suspendable` comes from `Runtime.suspends(name)` (`service.py:1010`), which reads
  `Executor.suspendable`, a `ClassVar`.
- `NativeExecutor.suspendable = True` (`native/executor.py:111`), for every native definition.
- `Gate.checkpoint_cancel_only` (`core/control.py:165`) explicitly refuses to act on PAUSE:
  "a pending PAUSE is left untouched rather than consumed with no parked body to suspend it in."
- `ToolCtx.safepoint` (`core/context.py:218`) raises `ConfigError` outright for a sync body.

So for a sync `@tool` there is no code path by which a pause can ever be honored, and
`run.can.pause` is `True`.

#### Current behavior

```
[sync]  can.pause before = True
[sync]  final=completed  pending-after=ControlSignal(verb=PAUSE, reason='operator stepped away')
        kinds=['run.started', 'run.completed']
[async] can.pause before = True
[async] final=completed  pending-after=ControlSignal(verb=PAUSE, reason='operator stepped away')
        kinds=['run.started', 'run.completed']
```

`run.pause()` returned without raising in both cases.

#### Expected behavior

`docs/design/execution-api.md:112` presents `run.can.*` as the answer to "is this control available
on this Run right now", with a worked row for a "plain callable" executor showing
`can.pause = False`. The design anticipated exactly this case and put the flag on the wrong object.

`deck.py:1440 Run.pause` promises `UnsupportedControlError` "if it can never take one".

#### Problem

`Executor.suspendable`'s own docstring says it is "whether it reaches a point where either can be
applied". For `NativeExecutor` that is a property of the definition, not of the executor. A UI
built on `run.can` renders an enabled Pause button that silently does nothing.

The stranded control-port row is the second half: `MemoryControlPort` and `SqliteControlPort` have
no expiry and no pruning, and `POLICY`'s terminal row can only consume an intent if something reads
the port, which nothing does for a finished run. In a long-lived deployment with a
`sqlite://` control port, the `signals` table accumulates one row per unhonored pause forever.

#### Real scenario

An approval console lists live runs with Pause/Cancel buttons driven by `run.can`. Every workflow
run shows both. Pressing Pause returns 200, the run keeps going, and the operator presses it again.

#### Impact

Surprising API, incorrect UI state, unbounded growth of the control-signal table.

#### Recommended direction

Either move suspendability to the `InvocableSpec` (the native executor can compute it from the
definition's `execution` and, if a checkpoint boundary is added, from nothing at all), or keep the
`ClassVar` and have `NativeExecutor` report `False` until EXEC-02 is resolved. Separately, prune a
run's control-port row when a terminal event is recorded. Do not implement here.

**Scope:** Multi-module.

---

### EXEC-04 -- `Deck.aclose()` is unbounded when a sync `@tool` body does not return

**Severity:** High
**Confidence:** High
**Modes affected:** Sync (thread)
**Category:** Shutdown / Resource

#### Evidence

`core/workers.py:49 SyncToolWorkers.aclose`:

```python
self._pool.shutdown(wait=False, cancel_futures=True)
running = [future for future in self._pending if not future.done()]
if running:
    await asyncio.gather(*(asyncio.wrap_future(future) for future in running), return_exceptions=True)
self._pool.shutdown(wait=True)
```

The `gather` has no deadline. `NativeExecutor.aclose` (`native/executor.py:170`) calls it after
cancelling every parked body, and `Deck.aclose` (`deck.py:962`) calls that inside its `try`, before
the `finally` that shuts MCP down and releases the process claim.

Every other wait on the shutdown path is explicitly bounded, deliberately and with a comment
saying why: `SHUTDOWN_TIMEOUT=10.0`, `CLOSE_TIMEOUT=5.0`, `REAP_TIMEOUT=1.0`
(`runtime/dispatch.py:62-76`), `_CLOSE_GRACE=1.0` and `_CLOSE_ATTEMPTS=2` (`deck.py:188-189`).
`dispatch.py`'s module docstring states the rule: "nothing here waits on one without a deadline,
not even shutdown".

#### Current behavior

Probe: one sync `@tool` blocking on a `threading.Event` nobody sets, then `deck.aclose()`.

```
RESULT: deck.aclose() did NOT return within 8s -- shutdown is unbounded
```

`MCPLifecycle.shutdown()` and `_release_process(self)` in the `finally` never run either, so a
subsequent `Deck(...)` in the same process is refused as well.

#### Expected behavior

Draining running work is the right default, and the existing test
(`tests/test_sync_tool_workers.py:258`) proves it does not deadlock the loop. The wait should still
have a ceiling, on the same reasoning the sink path already writes down.

#### Problem

A user's own code decides how long AgentDeck's shutdown takes, with no upper bound and no way to
express one. A container's `SIGTERM` grace period becomes a `SIGKILL`, and the events buffered in
the sinks are lost precisely because the graceful path never got to `drain()`.

#### Real scenario

A `@tool` calls a vendor SDK with no timeout. The vendor hangs. The pod is asked to roll. The
graceful shutdown never completes, Kubernetes kills the process after `terminationGracePeriod`, the
Langfuse buffer is discarded, and the run is left `RUNNING` in the log holding its session for the
full `stale_run_after` window.

#### Impact

Availability, lost telemetry, wedged sessions, undeliverable graceful shutdown.

#### Recommended direction

Give the drain a deadline of the same shape the sink path uses, log what was abandoned, and let the
daemon-thread question be decided explicitly rather than by omission. Do not implement here.

**Scope:** Local (`core/workers.py`).

---

### EXEC-05 -- An abandoned native run leaks its body task, its channel and its `_parked` entry

**Severity:** High
**Confidence:** High
**Modes affected:** Async, Sync (native executor)
**Category:** Resource / Cancellation

#### Evidence

`NativeExecutor.execute` (`native/executor.py:139`) registers the body in `self._parked[ctx.run_id]`
and pops it in exactly one place: `native/executor.py:143`, when the channel yields `None`, that is,
when the body ends **while `execute()` is still being read**.

`Runtime._play` wraps the executor stream in `aclosing`. Any exit other than a terminal payload,
which includes a consumer walking away and the run task being cancelled, throws `GeneratorExit`
into `execute()` at `await body.channel.next()`. That path does not pop, does not cancel the body
task, and does not close the channel. The comment at `native/executor.py:136-138` explains the
registration is deliberate ("must still leave this body reachable for aclose() to cancel"), which
covers the pause case but leaves the abandoned case unbounded.

#### Current behavior

Probe: start a native workflow, cancel the deck-owned execution task, release the body.

```
execution task cancelled -> run status=cancelled  parked bodies=['dea964d0-...']  finished=[]
after releasing the body -> finished=['body ran to the end'] parked=['dea964d0-...']
```

Three separate facts, all confirmed:

1. The log says `cancelled` while the body is still executing. This is the
   "Run state = cancelled, actual target = still running" mismatch, in the *async* mode.
2. The body ran to its end, with its side effects.
3. The `_parked` entry survives the body's own completion, holding the `_Body`, its
   `asyncio.Task`, and the `_Channel`'s unbounded queue with the final `RunCompleted` in it.

A server that serves N runs and has N clients disconnect accumulates N `_parked` entries for the
life of the `Deck`.

#### Expected behavior

`Deck.stream`'s docstring (`deck.py:1231`) says a caller that stops reading "only stops watching",
so a body outliving its reader is intended. Nothing intends the registry entry to outlive the body,
and `_play`'s `GeneratorExit` arm writing `run.cancelled` for a body that then completes is a lie
the log tells.

#### Problem

Two distinct issues share the path. The registry leak is a plain bug. The "cancelled in the log,
running in fact" mismatch is the same root cause as EXEC-02: nothing can stop an async body that
does not checkpoint, so the Runtime records the outcome it wants rather than the one it achieved.

#### Real scenario

A long-lived HTTP server. Clients disconnect mid-stream routinely. Memory grows monotonically, and
each abandoned workflow keeps executing to completion, writing to the database, while its log says
it was cancelled and its `run.completed` is refused by the sealed log.

#### Impact

Resource leak, leaked work, side effects after a recorded cancellation, log that disagrees with
reality.

#### Recommended direction

Pop and cancel the body in `execute()`'s `GeneratorExit`/`finally` path unless the run is genuinely
suspended (`payload.kind in SUSPENDED_KINDS`), which is already the distinguishing condition the
method computes one branch earlier. Do not implement here.

**Scope:** Local (`adapters/executors/native/executor.py`), with the semantic half belonging to
EXEC-02.

---

### EXEC-06 -- `Runtime._tree` grows without bound on a refused opening claim

**Severity:** High
**Confidence:** High
**Modes affected:** All
**Category:** Resource / State

#### Evidence

`Runtime.run` (`service.py:238-262`), in order:

```python
self.delegate(ctx.run_id, parent_run_id, spec.name, session_id)   # writes self._tree[run_id]
history = await self._history(ctx)
try:
    claimed = await self._claim_session(...)
except SessionBusyError:
    raise                                                          # tree entry stays
```

`_tree` entries are removed in exactly one place, `_rolling_up` (`service.py:1204`), which runs from
`_record` when a terminal payload is appended. A run whose claim was refused has no events at all,
so nothing ever pops it. `delegate` also increments `above.children` before the claim, so a refused
child permanently consumes one of the parent's eight fan-out slots.

`Runtime.close_cancelled` (`service.py:905`) writes its terminal event through `self._store.append`
directly rather than `_record`, so it also bypasses `_rolling_up` and leaks its entry.

#### Current behavior

Probe: one live run holding session `s1`, then twenty `runs.start(..., session_id="s1")` calls that
each raise `SessionBusyError`.

```
tree after 1 live run: 1
refused claims: 20  tree size now: 21
tree after the live run settled: 20
```

Twenty permanent entries from twenty ordinary, documented, expected errors.

#### Expected behavior

`_Delegation`'s own docstring says "one entry per run in flight, dropped when it ends". A run that
never started is not in flight.

#### Problem

`SessionBusyError` is not an exceptional path. `_claim_session` exists precisely because two
concurrent turns on one session are expected, and `_session_busy_message` writes three different
user-facing messages for it. Every one of those leaks 1 dict entry plus a `_Delegation` dataclass,
forever, in a process that is meant to run for weeks.

#### Real scenario

A chat surface where a user double-sends. Every double-send is one `SessionBusyError` and one
leaked entry. Over a month of production traffic this is unbounded memory growth with no
observable cause, since `_tree` is private and unmonitored.

#### Impact

Memory leak on a hot, expected error path; a delegation fan-out bound that only ever tightens.

#### Recommended direction

Place the run in the tree after the claim commits, or remove it in the failure arm. `_invoke` needs
the pre-claim placement to refuse a bound at the call site (`deck.py:1079`), so the child path
should undo its own placement when the child's claim fails. Do not implement here.

**Scope:** Local (`runtime/service.py`).

---

### EXEC-07 -- The cancel cascade is in-process only, and nothing says so

**Severity:** High
**Confidence:** High
**Modes affected:** All
**Category:** Cancellation / Contract

#### Evidence

`Runtime.signal` (`service.py:474`):

```python
if verb is Signal.CANCEL:
    for child in [child for child, placed in self._tree.items() if placed.parent == run_id]:
        await self.signal(child, verb, reason, namespace=namespace)
```

`self._tree` is per-`Runtime`, therefore per-process. The durable record of the parent edge lives on
`run.started.parent_run_id` (`core/events.py:102`) and is never consulted by `signal`.

#### Current behavior

A cancel issued on worker B against a parent executing on worker A cancels the parent (the control
port is shared when it is `sqlite://`) and reaches none of its children. The children keep running
on worker A, spending tokens, and complete normally.

#### Expected behavior

`Runtime.signal`'s docstring states it without qualification: "A **cancel cascades** to the runs
this one delegated, and theirs in turn. A parent that stops while a child keeps burning tokens is
worse than not offering cancel at all."

`docs/agentdeck-prd.md:63` lists cancel-cascade as an FR-4 deliverable.
`docs/design/execution-api.md:397` notes the readers of the parent edge "have to work on a log
nobody watched live", which reads as an intent this implementation does not meet.

#### Problem

Multi-process operation is a first-class, documented deployment: the `sqlite://` control port
exists specifically "to cross process boundaries" (`composition.py:158`), and
`tests/test_multiprocess_concurrency.py` runs six races across two real OS processes. The cascade
is the one control-plane guarantee that quietly does not cross that boundary, and the docstring
that states it is the one an operator reads.

#### Real scenario

Two uvicorn workers behind a load balancer. A run started on worker A delegates to three
sub-agents. The operator's cancel request is routed to worker B. The parent stops, the three
children run to completion, and the cost roll-up records nothing because the parent's terminal
event was already written.

#### Impact

Leaked work, uncontrolled spend, a stated guarantee that does not hold in the deployment it was
written for.

#### Recommended direction

Read the children off the store (a projection of `run.started.parent_run_id` for non-terminal runs)
when the in-process tree has no answer, or state the in-process scope in `signal`'s docstring and
in the PRD. Do not implement here.

**Scope:** Multi-module (`runtime/service.py` + `core/ports/store.py`).

---

### EXEC-08 -- `deck.run()` and `deck.stream()` record opposite outcomes for the same caller cancellation

**Severity:** High
**Confidence:** High
**Modes affected:** All
**Category:** State / API

#### Evidence

Both APIs call `Deck._start`, which hands the run to a deck-owned task. They differ in what they
then await:

- `Deck.run` (`deck.py:1223`) does `await task`. Cancelling the calling coroutine cancels the task
  it is awaiting, which reaches `_play`'s `except asyncio.CancelledError` arm and writes
  `run.cancelled` under a shield (`service.py:625`).
- `Deck.stream` (`deck.py:1256`) never awaits the task while streaming; it iterates
  `Deck._events`, which only reads the store. Cancelling the caller cancels a store read. The task
  is untouched and the run finishes normally.

#### Current behavior

```
deck.run   : runs=[('e17d488f','cancelled')] body_finished=['body ran to the end']
             kinds=['run.started','run.cancelled']
deck.stream: runs=[('b85c4840','completed')] body_finished=['body ran to the end']
             kinds=['run.started','run.completed']
```

Same workflow, same cancellation, same moment. One run is `cancelled` in the log, the other
`completed`. In both cases the body ran to its end.

#### Expected behavior

`Deck.stream`'s docstring is explicit and correct: "a caller that stops reading this generator ...
only stops *watching*. It does not stop the run." `Deck.run`'s docstring says nothing about
cancellation, and its behavior contradicts `stream`'s stated rule while still not stopping the
body.

`docs/design/run-identity.md` §9's whole point is that starting and observing are separate, which
`run` violates by coupling the caller's liveness to the run's recorded outcome.

#### Problem

`deck.run` writes a cancellation it did not achieve. The body kept running, so the log is wrong in
the same way EXEC-05 describes, and it is wrong only on one of the two front doors.

#### Real scenario

An ASGI handler wrapped in `asyncio.timeout` calls `await deck.run(...)`. The timeout fires. The
log says `cancelled`, an alerting rule counts it as an operator cancellation, and the workflow
completes 30 seconds later and writes its results, whose `run.completed` is then refused by the
sealed log.

#### Impact

Inconsistent observable state between two APIs over one run, a false cancellation record, a
silently discarded terminal event.

#### Recommended direction

Pick one rule for both. Given §9 and `stream`'s docstring, the rule should be that neither front
door's caller cancels the run: `Deck.run` would shield its `await task`, or await it in a way that
does not forward the cancellation. Do not implement here.

**Scope:** Local (`deck.py`).

---

### EXEC-09 -- `ctx.parallel()`'s all-or-nothing guarantee does not hold

**Severity:** Medium
**Confidence:** High
**Modes affected:** Async (native children)
**Category:** Cancellation

#### Evidence

`WorkflowCtx.parallel` (`core/context.py:372`) cancels the gather and calls `_abandon`, which calls
`run.cancel(...)` on each child that is not `WAITING_ANSWER`. That is a control-port write, subject
to EXEC-02 in full: a child with no `ctx.safepoint()` never sees it.

`tests/test_child_runs.py:212` proves the mechanism, and its `lingering` workflow calls
`await ctx.safepoint()` in a 500-iteration loop.

#### Current behavior

Probe with a sibling that has no safepoint:

```
right after the parent gave up: [('593b0d8e','failed'), ('faf6218e','failed'), ('306fd4ba','running')]
1.6s later:                     [('593b0d8e','failed'), ('faf6218e','failed'), ('306fd4ba','completed')]
sibling side effect: ['sibling ran to the end']
```

#### Expected behavior

`core/context.py:374`: "All-or-nothing: the first failure cancels the siblings and propagates, the
way `asyncio.TaskGroup` does, so no child is left running behind a parent that already gave up."
`docs/design/execution-api.md:388` repeats it.

`asyncio.TaskGroup` is the stated analogy, and a `TaskGroup` really does stop its siblings.

#### Problem

A named, documented structured-concurrency guarantee that holds only for bodies the author
instrumented. The test that guards it instruments the body, so the suite cannot see the gap.

#### Real scenario

`await ctx.parallel(ctx.invoke(charge_card, ...), ctx.invoke(reserve_inventory, ...))`. The charge
fails, the parent raises, and the inventory reservation completes anyway.

#### Impact

Correctness for side-effecting fan-out, and the false safety of a structured-concurrency promise.

#### Recommended direction

Follows EXEC-02. Until a native checkpoint boundary exists, the docstring should scope the
guarantee to children that reach a safe point. Do not implement here.

**Scope:** Multi-module.

---

### EXEC-10 -- A sync body's exception is discarded when a cancel is pending; an async body's is not

**Severity:** Medium
**Confidence:** High
**Modes affected:** Sync vs Async (native executor)
**Category:** State / Exception

#### Evidence

`native/executor.py:203-212`:

```python
if definition.execution is NativeExecution.THREAD:
    submit = self._workers.submit if self._workers is not None else asyncio.to_thread
    try:
        result = await submit(definition.call, **arguments)
    except Exception:
        await ctx.gate.checkpoint_cancel_only("tool_dispatch")   # may raise RunCancelledError
        raise
    await ctx.gate.checkpoint_cancel_only("tool_dispatch")       # may raise RunCancelledError
else:
    result = await definition.call(**arguments)                  # no checkpoint on either side
```

For THREAD, `checkpoint_cancel_only` raising replaces both a successful result and an in-flight
exception. For ASYNC neither replacement can happen, because there is no checkpoint.

#### Current behavior

Same race, opposite outcomes:

| body | result available, cancel pending | body raised, cancel pending |
|---|---|---|
| sync | `run.cancelled`, result discarded | `run.cancelled`, **the body's exception discarded** |
| async | `run.completed` | `run.failed` |

The sync behavior is deliberate and tested (`tests/test_sync_tool_workers.py:174` and `:218`, whose
docstrings state "the cancel takes precedence"). The async behavior is the absence of the same
rule, not a different decision.

#### Expected behavior

Whichever precedence rule is right, it should be the same rule in both branches. Today "cancel wins
a race with completion" is true for sync and false for async.

#### Problem

The tie-break for the single most timing-sensitive event in the lifecycle is decided by
`inspect.iscoroutinefunction` on the user's body.

#### Real scenario

Two `@tool`s do the same job, one sync and one async, and a monitoring dashboard counts cancelled
runs. Converting a tool from `def` to `async def` changes the metric with no other change.

#### Impact

Inconsistent terminal state, a lost user exception in one branch, undiagnosable behavior change on
a sync/async refactor.

#### Recommended direction

Resolve with EXEC-02: one checkpoint boundary applied to both branches, with one stated precedence
rule. Do not implement here.

**Scope:** Local.

---

### EXEC-11 -- `FAILED` is not sealed, so a "terminal" run can leave a terminal state

**Severity:** Medium
**Confidence:** High
**Modes affected:** All
**Category:** State

#### Evidence

`adapters/stores/__init__.py:_refuse_if_sealed` refuses an append only for `CANCELLED` and
`COMPLETED`. `STATES[RunStatus.FAILED].terminal` is `True` (`core/status.py:79`) and `status_of`
folds last-transition-wins (`core/status.py:291`).

#### Current behavior

Direct store probe:

```
run.failed    then run.cancelled -> ACCEPTED                status now cancelled
run.failed    then run.completed -> ACCEPTED                status now completed
run.cancelled then run.completed -> REFUSED (RunStateError) status now cancelled
run.completed then run.cancelled -> REFUSED (RunStateError) status now completed
```

#### Expected behavior

`docs/design/run-lifecycle.md`'s state diagram has no edge leaving `FAILED`, and the file states
"**Terminal** means no outgoing transition and nothing owed."

#### Problem

The gap is deliberate at the *reporter* level and documented there
(`context.mdx:91`: "after a `run.failed` it can still land, since a failure does not seal the log"),
which is a good decision for `report` events. It was not narrowed to non-lifecycle payloads, so a
lifecycle event is accepted too. The path that reaches it in production is
`_close_abandoned` (`service.py:854`), which writes `RunFailed` over a run a takeover judged stale;
if that run was in fact alive, its own `run.completed` lands afterwards and the status flips.

#### Real scenario

A worker's event loop stalls past `stale_run_after`. Another worker takes the session and writes
`run.failed(cancelled_hard)`. The first worker recovers and writes `run.completed`. The run's status
is `completed`, the session was already handed to a different turn, and two workers believe they own
the conversation.

#### Impact

The state machine's central invariant is representable-but-false; a takeover can be silently
reversed.

#### Recommended direction

Either seal on `FAILED` for lifecycle kinds while continuing to accept `report`, or amend
`run-lifecycle.md` and `STATES` so `FAILED` is not called terminal. The first is closer to the
stated design. Do not implement here.

**Scope:** Multi-module (`adapters/stores/__init__.py`, `core/status.py`, `docs/design/run-lifecycle.md`).

---

### EXEC-12 -- Sync user callables get three different execution treatments

**Severity:** Medium
**Confidence:** High
**Modes affected:** Sync
**Category:** API / Async

#### Evidence

| callsite | file:line | a `def` body runs | `ctx.safepoint()` in a `def` body |
|---|---|---|---|
| `@tool` played as a run | `native/executor.py:204` | `SyncToolWorkers` pool | `ConfigError`, `_loop` is set |
| `@tool` inside an agent turn | `authoring/tools.py:175` | `SyncToolWorkers` pool | `ConfigError`, `_loop` is set |
| `instructions=` callable | `authoring/instructions.py:71` | **inline on the event loop** | returns a coroutine the sync body cannot await; the gate is never read |
| `hooks=` methods | `authoring/hooks.py:105` | **inline on the event loop** | same |

`instructions.py:70` states the choice for instructions and gives a reason: "A sync body runs inline
rather than on a thread, which is what the SDK does with a sync instructions callable of its own:
this is a prompt string, not a tool call." No such reasoning exists for `hooks`.

#### Current behavior

A blocking `def` in `hooks=` (`on_tool_start`, `on_handoff`, ...) runs on the event loop. It stalls
every concurrent run on that loop, the lease renewer (`service.py:698`, whose own `ponytail:` note
already flags this exact failure), every sink consumer, and every `Gate` checkpoint.

`ToolCtx(run)` built at `hooks.py:105` and `instructions.py:69` has `_loop=None`, so
`ctx.safepoint()` is not refused there; in a sync body it produces an un-awaited coroutine and a
`RuntimeWarning`, and in an async body it raises `ControlSignalled` into the SDK's instruction
resolution, failing the run.

#### Expected behavior

`docs-site/content/build-your-deck/context.mdx:81` states one rule for the whole `ToolCtx` surface:
"a sync body runs on a worker thread with no await point to suspend on, so it raises `ConfigError`
there". Two of the four callsites that build a `ToolCtx` do neither.

#### Problem

"Sync bodies do not run on the event loop" is presented as an AgentDeck property. It is a property
of two of its four injection points.

#### Real scenario

An author adds an `on_tool_start` hook that writes an audit row with a blocking DB driver. Latency
across every concurrent run on that worker goes up by the driver's round trip, and a run whose lease
TTL elapses during a slow hook is taken over as dead.

#### Impact

Event-loop stalls, lease loss under load, an inconsistent `ToolCtx` contract.

#### Recommended direction

Route a sync `hooks=` body through the same pool a sync `@tool` uses, and set `_loop` on the
`ToolCtx` built at both sites so `safepoint()` refuses consistently. Keep instructions inline if the
stated reasoning holds, but say so in the docs table. Do not implement here.

**Scope:** Multi-module (`authoring/hooks.py`, `authoring/instructions.py`).

---

### EXEC-13 -- `ControlPort` and `LeasePort` are never closed

**Severity:** Medium
**Confidence:** High
**Modes affected:** All
**Category:** Resource

#### Evidence

`build_runtime` (`composition.py:88`) constructs both via `resolve_control_port()` and
`resolve_lease_port()` and hands them to `Runtime`. `SqliteControlPort.__init__`
(`control/sqlite/port.py:105`) and `SqliteLeasePort.__init__` (`leases/sqlite/port.py:72`) each open
a `sqlite3.Connection` at construction. Both classes have a `close()`.

`grep -rn "control.close\|lease.close" agentdeck/ tests/` finds three call sites, all in tests.
`Runtime` has no `aclose`. `Deck.aclose` (`deck.py:949-965`) closes the sinks, the sessions, each
executor and the store, and never touches either port.

#### Current behavior

With `AGENTDECK_CONTROL=sqlite:///signals.db`, every `Deck` opened in a process leaks two open
SQLite connections plus their WAL and SHM handles. The connections are only released when the
interpreter exits.

#### Expected behavior

`Deck.aclose`'s docstring states the ownership rule explicitly: "close what this Deck itself opened,
leave the rest". Both ports are opened underneath this Deck's own `__aenter__`.

#### Problem

Two of the seven adapters a Deck causes to exist have no owner. `_aclose_store` already exists as
the pattern for exactly this (duck-typed `aclose`/`close`), so the omission is an oversight rather
than a decision.

#### Real scenario

A test suite or a worker that opens a Deck per job accumulates file descriptors until the process
hits `RLIMIT_NOFILE`.

#### Impact

File-descriptor leak, WAL files held open, no clean release point.

#### Recommended direction

Give `Runtime` an `aclose` that releases the ports it was built with, called from `Deck.aclose`
alongside the store, respecting the same "only what this Deck built" rule. Do not implement here.

**Scope:** Multi-module.

---

### EXEC-14 -- `SyncToolWorkers` ownership is split between `Deck` and `NativeExecutor`

**Severity:** Medium
**Confidence:** High
**Modes affected:** Sync
**Category:** Resource

#### Evidence

Constructed at `deck.py:587` in `Deck.__init__`. Handed to three consumers:
`compile_agent(..., workers=self._sync_workers)` at `deck.py:760` and `deck.py:1140`, and
`NativeExecutor(..., self._sync_workers)` at `deck.py:822`. Closed at `native/executor.py:171`,
inside `NativeExecutor.aclose`.

Two paths never close it:

1. A `Deck` that is constructed (or `build()`-ed) and never opened. `Deck.aclose` iterates
   `self._executor_instances or ()`, which is `None` in that state.
2. A `Deck` opened with the private `_executors=` seam (`deck.py:812`). The caller's executors are
   used, `compile_tool` still received the deck's pool at `build()` time, and no `NativeExecutor`
   exists to close it.

#### Current behavior

The pool object leaks in both cases. In practice no threads are leaked, because
`ThreadPoolExecutor` spawns lazily and path 1 never submits, but path 2 can submit through the
compiled tool bridge and then leave the threads to the interpreter.

#### Expected behavior

`core/workers.py:22`'s docstring says the pool "knows nothing of `Run` lifecycle" and
`native/executor.py:123` says "Owned here, closed here". The two statements disagree about who
"here" is.

#### Problem

The resource is created by the composition root and destroyed by an adapter, so its lifetime is
whichever adapter happens to be present.

#### Impact

Maintainability, a latent thread leak on the `_executors=` path.

#### Recommended direction

Close it where it is constructed: `Deck.aclose`, after the executors. `NativeExecutor` keeps the
reference and stops owning the close, or keeps owning it and starts constructing it. Do not
implement here.

**Scope:** Local.

---

### EXEC-15 -- The gate's poll-reuse window can swallow a signal on a short run

**Severity:** Medium
**Confidence:** High
**Modes affected:** All
**Category:** Cancellation

#### Evidence

`Gate.checkpoint` (`core/control.py:148`):

```python
now = self._clock()
if self._polled_at is not None and now - self._polled_at < self._poll_interval:
    return
```

`CONTROL_POLL_INTERVAL = 0.2`. Every checkpoint inside 200ms of the previous one returns without
reading the port. If a run's remaining checkpoints all fall inside one window, the signal is never
seen, and after the run reaches a terminal state nothing reads its port row again.

#### Current behavior

The same probe, run twice, differing only by whether the cancel is followed by a 500ms sleep:

```
# no sleep: every remaining checkpoint falls inside one window
tool raised: []
final status: completed
kinds: [... 'run.completed']
pending left in port: ControlSignal(verb=CANCEL, reason='operator said stop')

# 500ms sleep: the next checkpoint reads
tool raised: ['RunCancelledError']
```

#### Expected behavior

The constant's docstring states the intended cost: "The cost is latency only, a signal is still
honored at a safe point, up to one interval late." That holds for a long run and fails for one that
ends inside the window.

#### Problem

The bound is described as a latency trade and is in fact a correctness trade at the tail of a run.
The stranded row then joins EXEC-03's accumulation.

#### Real scenario

A user clicks Stop on a run that is one token from finishing. The run completes, which is arguably
fine, but the log records no `control.requested` at all, so the UI cannot explain why the Stop had
no effect, and the row stays in the `signals` table forever.

#### Impact

An unrecorded control operation, unbounded control-table growth.

#### Recommended direction

Prune a run's control-port row when its terminal event is recorded, which fixes the accumulation for
this and for EXEC-03 in one place, and record `control.requested` even when the ruling is a no-op so
the log can explain the outcome. Do not implement here.

**Scope:** Local (`runtime/service.py` + `core/ports/control.py`).

---

### EXEC-16 -- Awaiting a cancelled run raises a bare `RuntimeError`, outside the error taxonomy

**Severity:** Medium
**Confidence:** High
**Modes affected:** All
**Category:** Exception / API

#### Evidence

`Run._result` (`deck.py:1592-1600`):

```python
if isinstance(payload, RunFailed):
    raise RuntimeError(f"run {self.id!r} failed: {payload.message}")
if isinstance(payload, RunCancelled):
    raise RuntimeError(f"run {self.id!r} was cancelled: {payload.reason}")
```

`core/errors.py` defines eleven `AgentdeckError` subclasses. Neither of these is one, and there is
no `RunCancelledError` in the public taxonomy (`core/control.RunCancelledError` is an internal
control-flow signal, not an error a caller catches).

`RunFailed.message` for a non-`InputError` failure is the exception's **type name only**
(`service.py:1329 _failed`), so a caller that did not execute the run itself loses the message and
the traceback.

#### Current behavior

`ctx.parallel`/`ctx.invoke` callers distinguish a cancelled child from a failed one by string
matching, which is what `tests/test_sync_tool_workers.py:203` does in the suite's own fixture
(`except RuntimeError: return "cancelled"`).

#### Expected behavior

`CLAUDE.md` §3: "Errors are part of the API: Every error must state what happened, why it happened,
and the exact code/action to resolve it." Every other refusal in the tree is a typed
`AgentdeckError` with an actionable message; `RunSuspendedError` was even added for the suspended
case on this exact method.

#### Problem

`except AgentdeckError` does not catch the two most common ways a run ends badly, and the two are
indistinguishable from each other and from any unrelated `RuntimeError` raised by user code.

#### Impact

DX, brittle caller code, an inconsistent error taxonomy on the most-used await path.

#### Recommended direction

Two members beside `RunSuspendedError`, carrying the run id, the status and the recorded reason.
Do not implement here.

**Scope:** Local (`core/errors.py`, `deck.py`).

---

### EXEC-17 -- `ctx.data` is shared across the loop and worker threads with no documented contract

**Severity:** Medium
**Confidence:** High
**Modes affected:** Sync, Async
**Category:** Context

#### Evidence

`RunContext.data` is `object`, held by reference (`core/context.py:109`). `ToolCtx.data` casts and
returns it (`core/context.py:187`). `Deck._invoke` passes `context=parent.data` to every child
(`deck.py:1085`), so a `ctx.parallel` fan-out hands the same object to N concurrently executing
children. A sync `@tool` receives the same object on a worker thread. There is no copy, no lock, no
`contextvars`, and no per-run isolation.

#### Current behavior

The object is genuinely shared. Two children mutating it race; a sync tool mutating it races with
the loop thread.

#### Expected behavior

`docs-site/content/build-your-deck/context.mdx` and `deck.py:1189` describe `context=` as "the
application's own environment for this run ... The same object serves the whole run, by reference".
Neither mentions concurrency. `context.mdx:109` covers "where it does not reach" (serialization,
HTTP) but not "who touches it at once".

#### Problem

The reference-sharing model is right and is the only thing that could work for a live DB handle. The
gap is that the concurrency obligation it creates is not stated anywhere, on a surface whose whole
selling point is that the user does not have to think about execution.

This is also the seam that would break first under any future process backend, since a by-reference
object cannot cross a pickle boundary.

#### Real scenario

`Deck(context=AppState(cache={}))`. A workflow fans out to four children with `ctx.parallel`, three
of which are sync tools writing to `ctx.data.cache`. The dict is mutated from four threads.

#### Impact

Latent data races in user code, and an undeclared constraint on the future sandbox/process boundary.

#### Recommended direction

State the rule in `context.mdx`: one object, shared by reference, concurrently reachable from the
loop and from every worker thread, so it must be safe for concurrent use or confined by the
application. Do not implement here.

**Scope:** Documentation, with an architectural implication for §14.

---

### EXEC-18 -- `MCPLifecycle` is process-global mutable class state with a loop-bindable lock

**Severity:** Low
**Confidence:** Medium
**Modes affected:** All
**Category:** Resource / Async

#### Evidence

`adapters/tools/mcp/lifecycle.py:58-62` holds `_servers`, `_failed`, `_connected`, `_config` and
`_lock` as class attributes. `shutdown()` (`:133`) clears `_connected` but leaves `_servers`,
`_failed` and `_config` populated. `reset()` (`:169`) clears everything and is marked "Tests only".

`_ensure_lock` caches one `asyncio.Lock` for the life of the process. CPython's
`_LoopBoundMixin._get_loop` binds a lock to a loop on its first *contended* acquire and raises
`RuntimeError(... is bound to a different event loop)` on any later loop. Verified against the
interpreter in use (3.14.3): `Lock.acquire` calls `_get_loop()` only on the contended path, so the
binding is latent rather than certain.

`Deck` supports sequential decks in one process (`_refuse_second_deck`, `deck.py:462`), and
`asyncio.run` creates a new loop each time.

#### Current behavior

A second sequential `Deck` in one process calls `MCPLifecycle.startup()`, which skips `configure`
for names already in `_servers` and then calls `connect()` on the *same* `MCPServer` objects the
first deck already `cleanup()`-ed, on a different event loop. The anyio task groups inside those
objects belong to the dead loop.

#### Expected behavior

`Deck.aclose`'s ownership rule and `_refuse_second_deck`'s support for sequential decks together
imply each Deck gets a clean MCP lifecycle.

#### Problem

MCP is the one subsystem whose state is process-scoped rather than deck-scoped, which is invisible
from `Deck`'s API.

#### Impact

Second-deck-in-one-process failures for any deployment using MCP; latent cross-loop lock error.

#### Recommended direction

Have `shutdown()` clear `_servers`/`_config` as well, and build the lock per `startup` rather than
per process. Do not implement here.

**Scope:** Local.

---

### EXEC-19 -- Two tasks are created with no owner and no exception observation

**Severity:** Low
**Confidence:** High
**Modes affected:** All
**Category:** Async / Resource

#### Evidence

1. `Runtime._holding` (`service.py:690-698`): `renewing = asyncio.create_task(self._renew(...))`,
   then in `finally` `renewing.cancel()` with **no `await`**. `_renew` catches `StoreError` but not
   other exceptions, so a lease backend raising anything else produces a
   "Task exception was never retrieved" at loop close.
2. `Runtime._close_cancelled` (`service.py:901`): `recording = asyncio.ensure_future(...)` then
   `with suppress(CancelledError): await asyncio.shield(recording)`. If the shield's wait is
   cancelled, `recording` keeps running detached with nobody holding or awaiting it. The docstring
   acknowledges best-effort delivery but not the unowned task.

Every other task in the package is registered, cancelled and awaited. These two are the exceptions.

#### Impact

Noise at shutdown, an unobserved write in the cancellation path, a small hole in an otherwise
complete ownership story.

#### Recommended direction

`await` the cancelled renewer with `suppress(CancelledError)`; keep a reference to the detached
recording so its exception is retrieved. Do not implement here.

**Scope:** Local.

---

### EXEC-20 -- `Runtime._reporting.fire` can drop another thread's report on a loop-closed race

**Severity:** Low
**Confidence:** Medium
**Modes affected:** Sync (thread)
**Category:** Reporter

#### Evidence

`service.py:1145-1158`:

```python
def fire(payload: Reported) -> None:
    if closed: return
    pending.append(payload)
    writing_it = settle()
    try:
        asyncio.run_coroutine_threadsafe(writing_it, loop)
    except RuntimeError:
        writing_it.close()
        pending.pop()          # removes the LAST item, not necessarily this one
        ...
```

`fire` is called from arbitrary worker threads (that is its purpose). Two threads reporting
concurrently while the loop is closing can have one thread's `pop()` discard the other's payload.
`deque.append`/`pop` are individually atomic, so this is a lost report and not corruption.

#### Impact

At most one lost report per racing pair, on a path that is already best-effort and already logs a
warning.

#### Recommended direction

`pending.remove(payload)` rather than `pop()`, or accept it and say so. Do not implement here.

**Scope:** Local.

---

### EXEC-21 -- `NativeExecution` is inferred, unnamed publicly, and has no process member

**Severity:** Low
**Confidence:** High
**Modes affected:** All
**Category:** API

#### Evidence

`core/invocable.py:NativeExecution` has two members, `ASYNC` and `THREAD`. It is set exactly once,
at `authoring/native.py:130`:

```python
execution = NativeExecution.ASYNC if inspect.iscoroutinefunction(inspect.unwrap(target)) else NativeExecution.THREAD
```

`@tool` and `@workflow` accept `name` and `description` only. No public API, setting, or
environment variable selects an execution mode. `grep -rn "subprocess|multiprocessing|fork|Popen"`
over `agentdeck/` matches only `testing.py`'s scripted HTTP server thread.

#### Current behavior

The execution mode is a derived, private fact. This is a good default and matches the philosophy
("user owns intent, AgentDeck owns machinery"). The finding is that the *consequences* of the
derived mode are user-visible and undocumented: `ctx.safepoint()` availability (EXEC-03), the
cancel/completion tie-break (EXEC-10), and whether the run's own thread can be interrupted at all.

#### Impact

A user changes `def` to `async def` for an unrelated reason and silently changes the run's
cancellation semantics.

#### Recommended direction

Keep the inference. Document the two modes and what differs between them in one table in
`build-your-deck/tools.mdx`, and remove the differences that are not inherent (EXEC-10). Do not
implement here.

**Scope:** Documentation, following EXEC-02 and EXEC-10.

---

## 6. Cancellation assessment

### The mechanism

Cancellation is cooperative everywhere and forced nowhere. There is no code path in `agentdeck/`
that kills a thread, terminates a process, or interrupts a running SDK loop. `result.cancel()` on
`RunResultStreaming` and `task.cancel()` on the native body task are the only two levers, and both
depend on the target reaching an `await`.

Signal flow, in full:

```
Run.cancel(reason)                                            deck.py:1464
  └─ _admits(CANCEL) → PRECONDITIONS[status, CANCEL]          refuses / no-ops on state
  └─ Deck._cancel → Runtime.signal(run_id, CANCEL, reason)    service.py:432
       ├─ cascade to self._tree children (IN-PROCESS ONLY, EXEC-07)
       ├─ if suspended: _cancel_suspended → claim + _terminate → run.cancelled NOW
       └─ else: ControlPort.signal(id, CANCEL, reason)        one row, no delivery guarantee
                     │
                     ▼   read only at a Gate checkpoint, at most once per 200ms
              Gate.checkpoint / checkpoint_cancel_only         core/control.py:139 / :165
                     │  consume() first, then raise RunCancelledError
                     ▼
              ControlSignalled caught by                       who catches decides everything
                ├─ NativeExecutor._play:214  → emits requested/observed/cancelled  ✅
                ├─ OpenAIAgentsExecutor:136  → result.cancel(), yields the three   ✅
                └─ the Agents SDK's function-tool error handler → TOOL ERROR       ❌ EXEC-01
```

### Ten questions

| # | question | answer |
|---|---|---|
| 1 | who initiates | `Run.cancel`, `ctx.parallel._abandon`, the AGUI binding on disconnect (ruling 46), `Runtime.signal` recursively |
| 2 | how it propagates | one row in a `ControlPort`; polled, never pushed |
| 3 | cooperative or forced | cooperative, always, in every mode |
| 4 | child work | cascaded via `Runtime._tree`, in-process only (EXEC-07), and each child is subject to the same cooperativeness |
| 5 | Reporter | unaffected; `settle()` flushes reports before the terminal event (`service.py:603`) |
| 6 | Context | unaffected; `ctx.data` is the caller's object and is never touched |
| 7 | Run state | `CANCELLED` if a checkpoint was reached, or if the run was suspended when the cancel arrived; otherwise whatever the run reaches on its own |
| 8 | caller observes | `RuntimeError("run ... was cancelled: ...")` from `Run.__await__` (EXEC-16); `asyncio.CancelledError` if the caller's own task was cancelled |
| 9 | resources | the native body task and its channel leak on the abandoned path (EXEC-05); the pool future is untracked by the awaiter but still drained by `aclose` |
| 10 | race with completion | resolved differently per mode (EXEC-10); a terminal event already in the log seals it (`_refuse_if_sealed`), except after `run.failed` (EXEC-11) |

### Edge cases

| case | behavior | verdict |
|---|---|---|
| cancel before execution starts | `Gate`'s first checkpoint always reads (`_polled_at is None`), so an agent turn honors it at once | correct, tested |
| cancel while executing | see the four-row table in §3 | **inconsistent** |
| cancel immediately after completion | `PRECONDITIONS[terminal, CANCEL]` is `NO_OP`, `Run.cancel` returns quietly | correct |
| cancel twice | second write overwrites the row (`ON CONFLICT DO UPDATE`); one effect | correct |
| cancel during exception handling | THREAD: replaces the user exception (EXEC-10). ASYNC: no checkpoint, unreachable | **inconsistent** |
| cancel during reporter emission | reports are settled before the terminal event; a report made after is refused by the sealed log and dropped with a log line | correct, documented |
| cancel during shutdown | `Deck.aclose` cancels the task twice with a 1s grace, then `close_cancelled` marks the run abandoned so `_record` refuses further appends | correct |
| parent cancelled while child continues | cascade covers in-process children only, and each child is cooperative | **incomplete** (EXEC-07, EXEC-09) |
| cancel inside the 200ms poll window at end of run | never observed, row stranded | **defect** (EXEC-15) |

### Specific hazards from the brief

| hazard | present | where |
|---|---|---|
| orphan asyncio tasks | **yes** | `NativeExecutor._parked` on the abandoned path (EXEC-05); two unowned tasks (EXEC-19) |
| threads continuing after Run cancellation | **yes, by construction** | `asyncio.wrap_future` cancellation does not stop a started `concurrent.futures` job; acknowledged and tested |
| processes continuing after Run cancellation | n/a | no process backend |
| inconsistent final states | **yes** | EXEC-08 (`run` vs `stream`), EXEC-10 (sync vs async) |
| cancellation converted into generic errors | **yes** | EXEC-01 (tool error), EXEC-16 (`RuntimeError`) |
| cancellation swallowed accidentally | **yes** | EXEC-01, EXEC-15 |
| races between result and cancellation | **yes, resolved differently per mode** | EXEC-10 |
| leaked resources | **yes** | EXEC-05, EXEC-06, EXEC-13 |

`_cancelling_ourselves()` (`dispatch.py:79`) deserves explicit credit: the codebase gets the
"is this `CancelledError` mine?" distinction right in every place it matters on the sink path, which
is the single most commonly botched piece of asyncio cancellation handling. The defects above are
about *reachability* of the cancellation, not about handling it once it arrives.

---

## 7. Shutdown assessment

### The six layers

| layer | entry point | what it does | deterministic | idempotent | bounded | graceful |
|---|---|---|---|---|---|---|
| cancel one Run | `Run.cancel` | one control-port row | yes | yes | yes | cooperative, see §6 |
| stop one tool execution | none | no API exists; a tool stops when its run does | n/a | n/a | n/a | n/a |
| stop one workflow | `Run.cancel` | as above | yes | yes | yes | see §6 |
| stop a binding | `Exposure._lifecycle` finally | cancel stdio task, `binding.stop()` in reverse order, then the deck if owned | yes | no guard | stdio task awaited unbounded | yes |
| shut down the worker pool | `SyncToolWorkers.aclose` | refuse new, drop queued, **drain running with no deadline** | yes | yes (`shutdown` is idempotent) | **no** (EXEC-04) | yes |
| shut down AgentDeck | `Deck.aclose` | the full sequence below | yes | yes (`_closed` guard) | **no**, inherits EXEC-04 | yes |

### `Deck.aclose` ordering, as implemented

```
_closed guard  (idempotent, terminal)
  ▼
for each live execution task:                       deck.py:922-950
    cancel · wait 1s shielded · retry once
    still alive → Runtime.close_cancelled(run_id)   marks abandoned, writes run.cancelled
  ▼
_executions.clear()
  ▼   try:
Runtime.drain()                                     flush + close every SinkDispatch
    per sink: flush(10s) → quiesce → cancel consumer → reap(1s) → sink.close(5s)
  ▼
ExecutionStore.aclose()                             SDK session store
  ▼
for each executor: aclose()
    NativeExecutor: cancel + await every parked body, then SyncToolWorkers.aclose()   ← UNBOUNDED
  ▼
store close, if owned
  ▼   finally:
MCPLifecycle.shutdown()                             partial, see EXEC-18
_state = CLOSED · _release_process()
```

Assessment against the brief's checklist:

| question | answer |
|---|---|
| stop accepting work? | **no explicit step.** `_require_open` refuses once `_state != "OPEN"`, but `_state` is set in the `finally`, so a `deck.run()` racing `aclose()` can still start a run that the already-drained `_executions` loop will not see |
| cancel active work? | yes, first, with two attempts and a 1s grace each |
| wait for active work? | yes, shielded, so a grace that runs out leaves the task rather than cancelling the close |
| flush reporter? | yes, `Runtime.drain` before the store closes; each sink gets a bounded flush and a bounded `close` |
| terminate workers? | drained, never terminated, with no deadline (EXEC-04) |
| close resources? | store yes if owned, sessions yes, MCP partially, **control and lease ports never** (EXEC-13) |

### Errors during shutdown

Handled well. `Deck.__aenter__`'s rollback path (`deck.py:857-885`) unwinds observers, the runtime
and the store on any `BaseException` and marks the deck `CLOSED` so a retry cannot half-succeed
(#572, #617). `Exposure._lifecycle` keeps the first error and still runs every remaining teardown
step. `SinkDispatch._close_sink` treats a raising or hanging sink `close` as telemetry loss rather
than a shutdown failure. `_close_abandoned` swallows a `StoreError` and leaves the run for the next
turn rather than wedging the current one.

Two gaps: the ordering gap above (no explicit "stop accepting work"), and the unbounded pool drain,
which means an error is not the failure mode to worry about, a hang is.

---

## 8. Run state-machine assessment

Full detail is in `analysis/EXECUTION_STATE_MACHINE.md`. Summary:

The declared machine is excellent. Six states, seven lifecycle kinds, four total tables
(`STATES`, `TRANSITIONS`, `PRECONDITIONS`, `POLICY`) built by comprehension over the enums so a
missing cell is an import-time `KeyError`, no status column anywhere, and the fold is the only
reader. `docs/design/run-lifecycle.md` documents it, including a dated drift record with verdicts.
This is the strongest part of the system.

Three findings against it:

| # | issue | severity |
|---|---|---|
| EXEC-11 | `FAILED` is declared terminal but the store does not seal it, so `FAILED → CANCELLED` and `FAILED → COMPLETED` are accepted and change the status | Medium |
| EXEC-08 | the same caller action produces `CANCELLED` through `deck.run` and `COMPLETED` through `deck.stream` | High |
| EXEC-05 | `RUNNING → CANCELLED` is written while the target is still executing, so the state leads the reality | High |

No illegal transition is *declared*. Every problem above is a transition written by a caller that
had not actually achieved the thing the transition asserts.

---

## 9. Context propagation assessment

| question | answer |
|---|---|
| shared, copied, serialized, reconstructed or proxied? | **shared**, always, by reference. `RunContext` is `frozen=True, slots=True`; `replace()` produces new envelopes around the same `data`. |
| mutation behavior | AgentDeck never reads or writes `data`. `RunContext` itself is immutable, except `tool_failures`, a mutable dict deliberately used as the Agents SDK's out-of-band error channel. |
| visibility of changes | immediate and total, since there is one object. |
| thread safety | **none provided, none documented** (EXEC-17). The same object reaches worker threads and concurrent children. |
| process boundaries | does not cross. Deliberate and well documented: `run(context=...)` is resupplied on `resume`/`answer`, and `Run.__init__`'s docstring says a handle rehydrated after a restart "has durable state and no context". |
| lifetime | one run, by reference, for the run's whole life; a child inherits the parent's by reference (`deck.py:1085`). |
| cleanup | none needed; AgentDeck holds no copy and never serializes it. `repr=False` on `data` and `tool_failures` so a logged context cannot leak a DB client. |

### APIs whose behavior changes with the execution mode

| API | async body | sync body | inside an agent turn |
|---|---|---|---|
| `ctx.data` | same object | same object, other thread | same object |
| `ctx.reporter` | works | works | works |
| `ctx.agent` | works | works | works |
| `ctx.safepoint()` | parks (native) / raises (agent tool) | **`ConfigError` at call time** | **raises into the SDK** |
| `ctx.ask()` | parks | n/a (`WorkflowCtx` requires async) | n/a |
| `ctx.invoke()` | works | n/a | n/a |

Only `safepoint()` differs, and it differs three ways rather than two, which is EXEC-01 and EXEC-03.

**On the brief's warning** ("a user should not accidentally depend on shared-memory semantics if
process execution cannot provide them"): today they can and do, because there is no process mode to
contradict them. Every published example passes a live client through `context=`. If a process
backend ever ships, `ctx.data` is the boundary that breaks, and it will break silently at
pickle time rather than at declaration time. This belongs in §15's sandbox-boundary list.

---

## 10. Reporter propagation assessment

| question | answer |
|---|---|
| where it lives | one `Reporter` per run, bound in `Runtime._bind` onto the frozen `RunContext`; `ToolCtx.reporter` is a plain read of it |
| sync or async internally | the **API** is synchronous by contract in every mode (`core/reporting.py:26`). The **write** is asynchronous: append to a `deque`, then `asyncio.run_coroutine_threadsafe(settle(), loop)` |
| how each mode sends | identically. That is the design's best call: there is one `Reporter` class, one `write` signature, and no mode-specific subtype anywhere |
| ordering guaranteed | yes, between reports: `settle` holds an `asyncio.Lock` and drains the deque in order. Between a report and a run event, no: a report is written on the loop's next turn, so it can land after an event the body produced later |
| during cancellation | `settle()` runs before every terminal write (`service.py:603`, `_sealing`), so reports made before the end are recorded ahead of it |
| during shutdown | `Runtime.drain` flushes each sink with a 10s deadline, then gives each sink a 5s `close`. Events already in the store are never lost; sink delivery is explicitly best-effort |
| after run completion | refused by the sealed log, `closed` latches, one `INFO` line, silently dropped thereafter (`service.py:1138-1140`) |
| late reports possible | yes, after `run.failed`, which does not seal the log (EXEC-11); documented at `context.mdx:91` |
| reports can be lost | yes, three ways, all deliberate and logged: the run's loop is gone (`RuntimeError` arm, `service.py:1153`), the log is sealed, or a sink drops it. Plus one accidental way (EXEC-20) |

### Cross-mode comparison

| | async body | sync body (thread) | agent turn | agent's tool |
|---|---|---|---|---|
| API shape | `ctx.reporter.info(...)`, no `await` | identical | identical | identical |
| write path | deque + `run_coroutine_threadsafe` (same thread) | deque + `run_coroutine_threadsafe` (cross-thread) | identical | identical |
| ordering vs events | one loop turn behind | one loop turn behind | one loop turn behind | one loop turn behind |
| loss on close | same | same | same | same |

**The Reporter is the cleanest abstraction in the system.** One logical contract, one class, no
mode-specific variant, and the cross-thread hand-off is entirely internal. The `ponytail:` note at
`core/reporting.py:32` correctly identifies the only real gap (a consumer reading the Runtime's
generator sees a report only at the engine's next payload, #487 item 2) and scopes it as latency.

The one structural question worth flagging: the reporter is bound to the loop that called
`Runtime.run`, captured once in `_reporting`. That is the correct scope today. It is also the
second thing (after `ctx.data`) that a process backend would have to replace, since a report from a
child process cannot reach that loop.

---

## 11. Exception model assessment

| class | originates | caught at | transformed to | Run state | caller sees | Reporter/log sees |
|---|---|---|---|---|---|---|
| user code in a native body | `definition.call` | `execute()`'s `await body.task`, then `_play`'s `except Exception` | `RunFailed(engine_error, "<TypeName> in engine 'native'")` | `FAILED` | the original exception with traceback, via `await task` | `run.failed`, type name only |
| user code in a native body, cancel pending, THREAD | as above | `checkpoint_cancel_only` replaces it | `RunCancelled` | `CANCELLED` | `RuntimeError("... was cancelled")` | `run.cancelled`, **the exception is not recorded** |
| user code in a tool inside an agent turn | the tool body | the Agents SDK | tool-error string + `ctx.tool_failures[call_id]` | unchanged, run continues | nothing | `tool.call.completed.error` |
| `InputError` | argument binding (`_arguments`) | `_play`'s `except Exception` | `RunFailed(invalid_input, str(exc))` | `FAILED` | the original | `run.failed` **with the message** (#621) |
| `ControlSignalled` (cancel/pause) | `Gate.checkpoint` | native `_play` / agents executor / **the SDK** | three payloads, or a tool error (EXEC-01) | `CANCELLED`/`PAUSED`, or unchanged | `RuntimeError` or nothing | three events, or none |
| `asyncio.CancelledError` | the caller or `Deck.aclose` | `_play`'s dedicated arm | `RunCancelled("consumer cancelled")` under a shield | `CANCELLED` | `CancelledError` | `run.cancelled` |
| `GeneratorExit` | a consumer closing the generator | `_play`'s dedicated arm | `RunCancelled("consumer stopped reading")` | `CANCELLED` | nothing | `run.cancelled` |
| `StoreError` | any store call | `_failed` special-cases it | `RunFailed(engine_error, "<TypeName> recording this run")` | `FAILED` | the original | `run.failed`, deliberately not blaming the engine |
| engine contract violation | an executor that stops without a terminal payload | `_play`'s post-loop check | `RunFailed(engine_error, "engine 'x' ended after 'y'")` | `FAILED` | nothing extra | `run.failed` + `logger.error` |
| `SessionBusyError` / `DuplicateKeyError` | `claim_start` | `Deck._start`, synchronously | itself | no run exists | the typed error | nothing |
| sink failure | `Observer.emit` | `SinkDispatch._emit` | counted, breakered, throttled log | unchanged | nothing | nothing |
| serialization | a tool returning a non-JSON value | nowhere | `repr()` into the log and the prompt | unchanged | nothing | corrupt value (published known issue #251) |
| process crash | n/a | n/a | n/a | n/a | n/a | n/a |

### Against the brief's checklist

| pattern | present |
|---|---|
| swallowed exceptions | **yes**, twice: EXEC-01 (control signal into a tool error) and EXEC-10 (a sync body's exception discarded by a pending cancel) |
| double wrapping | no. `_failed` produces one `RunFailed`, and the original is re-raised unwrapped |
| loss of traceback | **yes, at the process boundary.** A caller in the executing process gets the real exception via `await task`; any other reader gets `RuntimeError(payload.message)` where `message` is the type name |
| loss of original type | same boundary, same cause. Also EXEC-16: cancelled and remotely-failed both become bare `RuntimeError` |
| inconsistent across modes | **yes**, EXEC-10 |
| infrastructure mistaken for user failure | **no**, and this is done deliberately well: `_failed` special-cases `StoreError` so a log that could not be written is not reported as the engine misbehaving |

The message-redaction rule (`_failed`: "an exception message can carry content that must not reach
a sink") is a good decision that has a real cost: it is the direct cause of the traceback loss above.
The `InputError` carve-out shows the team already sees the tension.

---

## 12. Resource ownership assessment

Full table in §2.2. Judged against "any resource without clear ownership is suspicious":

| resource | owner | verdict |
|---|---|---|
| run-owning tasks | `Deck._executions` | **clear.** Registered at creation, popped on settle, cancelled at close, exception retrieved in `_execution_done` |
| native body tasks | `NativeExecutor._parked` | **unclear.** Popped on one of four exit paths (EXEC-05) |
| sink queues and consumers | `SinkDispatch` | **clear**, and the most carefully written lifecycle in the tree: bounded queue, bounded flush, bounded reap, explicit abandonment with a log line |
| lease renewer | `Runtime._holding` | **mostly clear**, cancelled but not awaited (EXEC-19) |
| shielded cancel write | none | **unclear** (EXEC-19) |
| `SyncToolWorkers` pool | split `Deck` / `NativeExecutor` | **unclear** (EXEC-14) |
| worker threads inside the pool | the pool | clear, and correctly non-interruptible by design |
| default-executor threads (`asyncio.to_thread` in the sqlite adapters) | the event loop | clear enough; the loop shuts them down |
| stdin pump thread | nobody, daemon | **clear by explicit decision**, with a comment saying why |
| event store | `Deck._owns_store` | **clear**, with a duck-typed `_aclose_store` for the shape inconsistency |
| control port | nobody | **missing** (EXEC-13) |
| lease port | nobody | **missing** (EXEC-13) |
| SDK session store | `Deck._sessions` | clear |
| MCP servers | process-global class state | **unclear** (EXEC-18) |
| `_Channel` queues | the `_Body` in `_parked` | leaks with it (EXEC-05) |
| `Runtime._tree` entries | `_rolling_up` | **leaks on refused claims and on `close_cancelled`** (EXEC-06) |
| `Runtime._abandoned` entries | nobody | bounded by deck lifetime, acceptable |
| control-port rows | `consume()` | **leak on every unhonored signal** (EXEC-03, EXEC-15) |
| Langfuse SDK resources | deliberately not owned | **clear by explicit decision**, with a measured justification in `Deck.aclose`'s docstring |

Six resources with a clear owner and an explicit decision behind it; six with a gap. The pattern in
the gaps is consistent: everything the `Deck` visibly creates is owned, and everything created one
level down by `build_runtime` or by an adapter's constructor is not.

---

## 13. Test-gap matrix

Full proposal in `analysis/EXECUTION_TEST_MATRIX.md`. Summary of what is *not* protected today:

| Mode | Success | Exception | Cancel | Shutdown | Timeout | Reporter | Context |
|---|---|---|---|---|---|---|---|
| agent turn (`openai-agents`) | ✅ | ✅ | ✅ | ✅ | n/a | ✅ | ✅ |
| native async body **with** `safepoint()` | ✅ | ✅ | ✅ | ✅ | n/a | ✅ | ✅ |
| native async body **without** `safepoint()` | ✅ | ✅ | ❌ **no test** | ❌ | n/a | ✅ | ✅ |
| native sync body (thread) | ✅ | ✅ | ✅ | ⚠️ bounded case only | n/a | ✅ | ✅ |
| `@tool` inside an agent turn, `safepoint()` | ✅ | ✅ | ❌ **no test** | ✅ | n/a | ✅ | ✅ |
| `instructions=` / `hooks=` sync body | ✅ | ✅ | ❌ | ❌ | n/a | ⚠️ | ✅ |
| cross-process (two workers, one store) | ✅ | ✅ | ✅ single run | ✅ crash | n/a | n/a | n/a |
| cross-process cancel **cascade** | n/a | n/a | ❌ **no test** | n/a | n/a | n/a | n/a |

The suite is large (1876 passing) and unusually good at the hard parts: six two-process races in
`test_multiprocess_concurrency.py`, a crash-reconciliation suite, a 49KB sink-dispatch suite that
tests the breaker and the cooldown as facts rather than sleeps, and a contract suite that runs the
same cases across every store.

**The structural gap is a single pattern.** Every cancellation test writes a body that calls
`await ctx.safepoint()`:

- `tests/test_native_workflow.py:327` (`looping` has a safepoint)
- `tests/test_child_runs.py:212` (`lingering` has a safepoint in a 500-iteration loop)
- `tests/test_child_runs.py:329` (paused child, which is suspended and so terminated by the claim)
- `tests/test_sync_tool_workers.py:174` (the sync path, where the executor supplies the checkpoint)

The suite therefore proves the cancellation *mechanism* works and never asks whether the
*contract* holds for the body a user writes by default. Five of the eight top findings live in that
blind spot.

Second gap, smaller: `Deck.aclose` boundedness is tested only with a cooperating tool
(`test_sync_tool_workers.py:258` releases the worker 0.1s into the close), so EXEC-04 is invisible.

Third gap: no test asserts that a resource registry is empty after teardown, which is why EXEC-05
and EXEC-06 survived.

---

## 14. Essential vs accidental complexity

### Essential

Complexity that is inherent to asyncio, threads and durable execution, and should not be abstracted
further.

| | why it is essential |
|---|---|
| cooperative cancellation | Python cannot interrupt a thread or a CPU-bound coroutine. Anything else would be a lie. |
| a started thread cannot be stopped | `concurrent.futures.Future.cancel()` returns `False` once running. `SyncToolWorkers`' docstring says so. |
| a paused agent turn replays; a paused native workflow parks | an agent turn has a checkpoint (the log), an imperative body's locals *are* its state. The two cannot be unified without durable replay. |
| a parked native body lives in one process | stated as the ceiling in `NativeExecutor`'s class docstring, and `_wake` raises a good error naming it. |
| `ctx.data` does not cross a process | a live DB handle cannot be serialized. Resupply-on-resume is the right answer. |
| store-assigned `seq` and `ts` | the only way a number cannot be spent without being persisted. ADR-D11. |
| conditional-append claims | the only check-then-write that is safe across processes. |
| sinks are lossy and bounded | NFR-6. A run pinned to its slowest reader is a liveness risk. Documented at length. |
| the log is the state, folded | removes an entire class of cache-coherence bugs. |

### Accidental

Complexity introduced by the current architecture, removable without losing function.

| | cost | finding |
|---|---|---|
| a per-executor `suspendable` flag describing a per-body capability | `run.can.*` lies for the majority of runs | EXEC-03 |
| `ctx.safepoint()` behaving three ways depending on who compiled the context | one call, three outcomes, none visible from the call site | EXEC-01, EXEC-03 |
| the native executor having no checkpoint boundary of its own while the agent executor does | pushes the whole cancellation contract onto the user | EXEC-02 |
| a cancel/completion tie-break chosen by `iscoroutinefunction` | changing `def` to `async def` changes the semantics | EXEC-10 |
| two front doors (`run`, `stream`) with different caller-cancellation semantics | two mental models over one run | EXEC-08 |
| four `ToolCtx` construction sites with three different sync policies | "sync runs off the loop" is true 50% of the time | EXEC-12 |
| the delegation tree existing only in memory | the cascade and the roll-up quietly stop at the process edge | EXEC-07 |
| resources built by `build_runtime` having no `aclose` counterpart | two adapters with no owner | EXEC-13 |
| a pool constructed in `Deck` and closed in an adapter | ownership depends on which adapter is present | EXEC-14 |
| `FAILED` declared terminal and not sealed | the state machine's own invariant is false | EXEC-11 |
| `RuntimeError` outside the error taxonomy on the main await path | `except AgentdeckError` misses the two commonest bad endings | EXEC-16 |

The ratio is telling. There is very little accidental complexity in the *event* path, the *state*
path or the *store* path. Almost all of it is on the control path, and almost all of that traces to
one root: **`Executor.suspendable` and `Gate` were designed for an executor that drives its own
loop, and the native executor does not drive one.**

---

## 15. Recommended priorities

### Correctness blockers

Behavior that can produce incorrect, unsafe or leaked execution. Fix before anything else.

| # | finding | one-line direction |
|---|---|---|
| 1 | EXEC-01 | do not let a `ControlSignalled` escape a compiled tool into the SDK's error handler |
| 2 | EXEC-02 | give the native executor a checkpoint boundary, or stop advertising the capability |
| 3 | EXEC-05 | pop and cancel the native body on the abandoned path, not only on the read-to-end path |
| 4 | EXEC-04 | put a deadline on `SyncToolWorkers.aclose`, the way every other shutdown wait has one |
| 5 | EXEC-06 | place a run in the delegation tree after its claim commits, or remove it on failure |
| 6 | EXEC-07 | resolve the cascade off the log, or scope the guarantee in the docstring and the PRD |

### Contract inconsistencies

Execution modes that violate the expected common contract. Fix after the blockers.

| # | finding | one-line direction |
|---|---|---|
| 7 | EXEC-03 | move suspendability off the executor `ClassVar`, and prune control rows at terminal |
| 8 | EXEC-08 | one caller-cancellation rule for `deck.run` and `deck.stream` |
| 9 | EXEC-10 | one cancel-vs-completion precedence rule for both native branches |
| 10 | EXEC-09 | make `ctx.parallel`'s promise true, or scope it |
| 11 | EXEC-11 | seal `FAILED` for lifecycle kinds, or stop calling it terminal |
| 12 | EXEC-12 | one sync policy for all four `ToolCtx` construction sites |
| 13 | EXEC-16 | two typed errors beside `RunSuspendedError` |
| 14 | EXEC-15 | record `control.requested` even for a no-op ruling, so the log can explain it |

### Simplification opportunities

Reduce accidental complexity without losing function.

| | opportunity |
|---|---|
| a | one checkpoint boundary owned by the Runtime rather than one per executor. `Gate` already lives in `core/`; the reason it is called from three places with three policies is historical, not structural. |
| b | delete `Gate.checkpoint_cancel_only` once (a) exists. It is a workaround for the native executor's missing boundary, and it is the direct cause of EXEC-03's pause hole and EXEC-10's asymmetry. |
| c | `Runtime.aclose()` mirroring `Deck.aclose`'s ownership rule, which absorbs EXEC-13 and gives the control-row pruning of EXEC-15 a home. |
| d | one ownership rule stated once: whoever constructs, closes. That resolves EXEC-13, EXEC-14 and EXEC-18 as instances of a rule rather than three separate patches. |
| e | document the two native execution modes in one table (EXEC-21) instead of leaving `NativeExecution` an internal enum whose consequences are user-visible. |

### Future sandbox boundary

Which parts of the current architecture should later become the boundary for sandboxed execution.
Named, not designed.

| boundary | why it is the right seam | what already fits | what does not |
|---|---|---|---|
| **`Executor.execute` (`core/ports/executor.py`)** | the primary boundary. An async generator of `KnownPayload` with `(spec, input, history, ctx)` in and payloads out, no envelopes, no `seq`, no namespace. A sandboxed executor is a new implementation, not a new contract. | `spec` (pydantic, `frozen`), `input`/`history` (pydantic `Event`s), every `KnownPayload` | `ctx`: `data` is `object`, `gate` and `reporter` are live objects |
| **`RunContext.data`** | the one field that is deliberately un-serializable, already documented as not crossing a restart. A sandbox needs it to be either absent, a declared serializable value, or a proxy. | the resupply-on-resume rule already teaches users it does not persist | every published example passes a live client through it (EXEC-17) |
| **`Reporter._write`** | already a single `Callable[[Reported], None]` seam, already crosses a thread. Making it cross a pipe is a change of transport, not of contract. | the sync API, the `Reported` payload | the captured `loop` in `Runtime._reporting` |
| **`Gate` / `ControlPort`** | already an out-of-process transport (`sqlite://`) polled by value. A sandboxed body polls the same port. | `ControlSignal` is a frozen dataclass of two strings | `ControlSignalled` is raised as a live exception with `payloads` built in-process |
| **`SyncToolWorkers.submit`** | the existing "run this callable somewhere that is not the loop" seam. A process backend is the same signature with a different submitter. | the `submit(func, *args) -> await result` shape | `func` and its arguments must become picklable |
| **`NativeExecutor._parked`** | the explicit statement that a live body lives in one process. A sandbox makes this boundary hard rather than soft. | `_wake`'s error already names the ceiling | nothing; this is the seam that must not silently widen |

`Deck._executions` and `Runtime._tree` should **not** become the boundary. Both are in-process
registries whose in-process-ness is already the source of EXEC-06 and EXEC-07; a sandbox built on
them would inherit both.

### Leave alone

Complexity that is inherent and should not be abstracted further.

| | why |
|---|---|
| `SinkDispatch` in full | bounded queue, drop-oldest, breaker with a clock-read cooldown, three separate shutdown deadlines, `_cancelling_ourselves()`. Every constant has a written justification. This is the reference for how the rest of the shutdown path should look. |
| the four lifecycle tables in `core/status.py` | total by construction, import-time `KeyError` on a missing cell, no second place a rule can be written. |
| store-assigned `seq`/`ts` and the conditional-append claims | the only correct answer for multi-worker operation, and the tests prove it across two real processes. |
| persist-before-yield | the ordering that makes the refetch promise true. |
| the `_failed` message-redaction rule | a deliberate privacy trade with a documented carve-out. Its traceback cost is real but is the smaller harm. |
| `ctx.data` being shared by reference | correct. It needs documentation (EXEC-17), not a mechanism. |
| the terminal binding's daemon stdin thread | the right call, with a comment saying why `os._exit` is not needed. |
| not owning the Langfuse SDK's threads | measured against langfuse 4.14.1, justified in the docstring, and the alternative is worse. |
| the absence of a run-level timeout | `RunContext`'s docstring states the rule: a field comes back with the thing that enforces it. Adding a deadline field without an enforcer would be the mistake. |

---

## Appendix A: probes

Every probe below was run against this worktree with `AGENTDECK_CONTROL=memory://` and a scripted
model where a model was needed. Sources are in the session scratchpad, not in the repository.

| probe | proves |
|---|---|
| `probe_cancel_modes.py` | THREAD cancels, ASYNC completes, same timing (EXEC-02, EXEC-10) |
| `probe_safepoint_in_agent_tool.py` | `ctx.safepoint()` with no `_channel` consumes the signal and raises (EXEC-01) |
| `probe_agent_tool_signal.py` | end to end: the raise becomes a tool error, run completes, no `run.cancelled`; and without the 500ms sleep, the signal is never read and is stranded (EXEC-01, EXEC-15) |
| `probe_lifecycle.py` | an abandoned run stays in `_parked` after its body finishes (EXEC-05) |
| `probe_caller_cancel.py` | `deck.run` records `cancelled`, `deck.stream` records `completed` (EXEC-08) |
| `probe_pause_modes.py` | `can.pause=True`, pause never honored, signal stranded, both modes (EXEC-03) |
| `probe_tree_leak.py` | 20 refused claims leave 20 permanent `_tree` entries (EXEC-06) |
| `probe_shutdown_bound.py` | `deck.aclose()` does not return within 8s behind a wedged sync tool (EXEC-04) |
| `probe_parallel_all_or_nothing.py` | a sibling with no safepoint completes after the parent gave up (EXEC-09) |
| `probe_failed_not_sealed.py` | `run.failed → run.cancelled` and `run.failed → run.completed` are accepted (EXEC-11) |
