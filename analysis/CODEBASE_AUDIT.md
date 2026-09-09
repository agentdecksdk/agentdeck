# AgentDeck Engineering Audit

**Subject:** `agentdeck-sdk` v6.0.3, tag `v6.0.3` (`b9a7f76`), clean detached worktree.
**Method:** full-tree read, gate run in the foreground, behavior traced through code and
verified with runnable probes where a claim mattered.
**Constraint:** read-only. Nothing in the product was changed.

Gate as measured here:

```
ruff            All checks passed
ty              All checks passed
import-linter   12 contracts kept, 0 broken
pytest          1876 passed, 188 skipped in 82.31s
coverage        92% of 6119 statements
```

---

## 1. Executive summary

AgentDeck is a **well-engineered runtime with a public API that has drifted away
from it**. The two halves should be judged separately.

The runtime half is genuinely good. The event log is the single source of truth,
`seq` and `ts` are assigned inside the write so a gap is real, the
persist-fan-out-yield order is honored everywhere, session claims are conditional
appends that hold across processes, and the whole lifecycle is one exhaustively
keyed table in `core/status.py` that fails at import if a state is added without
a rule. The store contract suite runs against four backends. There is a
multiprocess concurrency suite and a crash-reconciliation suite. This is the work
of people who have been paged.

The API half does not match. Five different ways to start a run, four ways to
read one back, four disjoint exception families, `-> Any` on the two decorators
that are the entire authoring surface, and a headline README example that raises
`ConfigError` when you run it. The stated philosophy is "core primitives should be
small, obvious, composable and predictable"; the code carries 5,274 lines of
docstring and comment across 16,590 lines of source (32%), which is the measurable
signature of an API that cannot be predicted without being explained.

**The philosophy is right and the runtime honors it. The surface does not.** The
gap is not caused by missing work; it is caused by every capability landing as a
new entry point instead of as a new argument or return value on an existing one.

Three things would move the product further than anything else on the list:

1. Make `ctx.invoke` accept an `Agent` object (AD-01, AD-02). The most-copied
   code sample in the project currently does not run.
2. Give `await deck.run(<workflow>)` a way to say "this did not complete"
   (AD-03). It returns `None` today.
3. Decide what `Deck.aclose()` owes a parked workflow (AD-04). Today it orphans
   the run, wedges its session permanently, and leaves an approval inbox showing
   an item that destroys the run when clicked.

None of the three is a large change. All three are on the path a first user takes.

### Where the philosophy itself is under strain

The audit was asked to challenge the philosophy, not only measure against it. Two
places where it does not survive contact with the implementation:

- **"One function away" is expensive for a system with durable state.** Going
  from `deck.run()` to `run.pause()` is not one function away; it is one function
  plus the knowledge that resuming an agent turn replays it and re-executes its
  tools. That fact cannot be made to disappear. The honest version of the promise
  is "one function away, and the function tells you what it costs", and the pause
  API does not.
- **"Prefer self-explanatory APIs over abstractions that require extensive
  documentation" is being enforced by writing the documentation into the code.**
  `deck.py` and `runtime/service.py` are 38% and 44% prose respectively. The
  prose is high quality and often the only place a rule is written down, which
  means it is load-bearing, which means the rule is not self-explanatory. This is
  worth naming because the project's own standards forbid the symptom
  ("Comments: Extremely rare") while the codebase depends on the cure.

---

## 2. System mental model

### 2.1 What a user has to hold in their head

| concept | why it exists | can a user predict it? |
|---|---|---|
| `Deck` | catalog + lifecycle | yes |
| `Agent` | declaration | yes |
| `@tool` / `@workflow` | the two kinds of Python AgentDeck runs | yes, and the tool/workflow split is a good rule |
| `ToolCtx` / `WorkflowCtx` | injected capabilities, split by kind | yes |
| `Run` | a handle, not a future | mostly; `await run` raising for a paused run is a surprise |
| `Event` + `seq` | the record | yes |
| `RunStatus` + `run.can` | lifecycle | yes for `can`, no for `pause()` on a `WAITING_ANSWER` run |
| `namespace` | opaque isolation key | yes |
| `session_id` | conversation, one turn at a time | yes |
| `key` | idempotency claim, permanent | needs the docstring |
| `TurnResult` vs a bare value | agents return the first, workflows the second | **no**, and it is the cause of AD-01 and AD-07 |
| observers + `views` | taps | yes |
| bindings + `Exposure` | protocol ingress | yes |

Twelve concepts to run one agent well. That is not unreasonable for the problem.
The one that does not pay for itself is the eleventh.

### 2.2 The execution model in one paragraph

A run is a row in an append-only log. `Deck` resolves a name, mints nothing, and
asks `Runtime` for an async generator. `Runtime` binds a `Gate` and a `Reporter`
onto a frozen `RunContext`, then makes a **conditional append** that both opens
the run and claims its session. `Deck` pulls exactly one event from that
generator (`run.started`, now durable) and hands the rest to a background task
that drains it, so the run advances whether or not anyone watches. Every
subsequent payload from the executor is persisted, fanned out to bounded sink
queues, then yielded, in that order. Reading a run is a separate act: `Deck._events`
tails the **store**, waking on the local task if there is one and polling at 50ms
if there is not. The engine notices control signals only at safe points, where the
`Gate` consults one table and raises a `ControlSignalled` carrying all three
events of an honored signal.

This model is correct and it is the right model.

### 2.3 State ownership at a glance

Durable: the event log (status is a fold over it, never a second table).
Process-global and mutable: `deck._live_deck`, `MCPLifecycle`'s four class
attributes, `get_settings`'s `lru_cache`, and OpenInference's instrumentation.
Per-Deck: execution tasks, minted agents, the sync worker pool.
Per-Runtime and in-memory only: the delegation tree.
Per-executor and in-memory only: parked workflow coroutines.

The last two are where the design's promises exceed what it can deliver across
processes.

---

## 3. Top 10 findings

| # | ID | title | severity |
|---|---|---|---|
| 1 | AD-01 | The README's headline example raises `ConfigError` | Critical |
| 2 | AD-03 | `await deck.run(<workflow>)` returns `None` for a cancelled or paused run | High |
| 3 | AD-04 | Closing a Deck orphans a parked workflow: session wedged, run unresumable, inbox poisoned | High |
| 4 | AD-02 | `ctx.invoke` takes tool and workflow objects but only agent *names* | High |
| 5 | AD-07 | Returning a delegated agent's result from a workflow fails with a raw pydantic error | High |
| 6 | AD-05 | `MCPLifecycle` never clears its registry, so sequential Decks reuse torn-down servers | High |
| 7 | AD-06 | `await run` raises bare `RuntimeError`, outside the documented taxonomy | High |
| 8 | AD-08 | Resuming a paused agent turn re-executes its tools, and no user-facing doc says so | High |
| 9 | AD-09 | `Agent.run()` is a second execution path that bypasses the entire runtime | High |
| 10 | AD-10 | `Deck.aclose()` leaks sessions, executors and the store if `drain()` raises | Medium |

---

## 4. Full findings

### [AD-01] The README's headline example raises `ConfigError`

**Severity:** Critical **Area:** API / Docs **Confidence:** High

**Evidence**

`README.md` lines 50-75, the first code block a reader meets:

```python
@workflow
async def handle_request(ctx: WorkflowCtx, ticket: dict) -> str:
    response = await ctx.invoke(agent, ticket["query"])
    ...
    return response
```

`agentdeck/deck.py:277` `_invoked_name` accepts `str`, `AgentInstance`, and
`NativeDefinition`. An `agentdeck.authoring.agent.Agent` is none of those, so it
falls to the `raise ConfigError` at line 300.

Run verbatim against a scripted model:

```
RAISED: ConfigError ctx.invoke() takes a catalog name, a @tool/@workflow
definition, or an agent from ctx.agents; got a Agent.
```

Correcting it to `ctx.invoke("SupportBot", ...)` then fails differently, because
`return response` returns a `TurnResult` from a function annotated `-> str`:

```
RAISED: ValidationError 1 validation error for DataBlock
  input was not a valid JSON value [input_type=TurnResult]
```

**Problem**

The project's primary code sample does not run, and has two independent defects.
`tests/test_docs_site.py` parses `README.md` but `tests/test_docs_examples.py`
executes only `docs-site/content/**` fences carrying a `run` meta token, so the
README is outside the one harness that would have caught this.

**Why it matters**

This is the first code a prospective user copies. It fails on line 3 with an error
telling them to use `ctx.agents`, which is a different feature. For a project
whose thesis is "make correct agentic software simple", the onboarding path
failing at the first composition is the most expensive possible defect.

**Recommended direction**

Two independent changes, both small: extend `_invoked_name` to accept an `Agent`
the deck holds (AD-02), and add `README.md` to the stage-2 fence executor. Fix the
sample's `-> str` in the same pass.

**Scope:** Local (the fix), Multi-module (the test harness change).

---

### [AD-02] `ctx.invoke` takes tool and workflow objects but only agent names

**Severity:** High **Area:** API **Confidence:** High

**Evidence**

`agentdeck/deck.py:277-313` (`_invoked_name`). Accepted: `str`,
`AgentInstance` (from `ctx.agents.create/fork`), `NativeDefinition` (from
`@tool`/`@workflow`). Refused: `Agent`.

`Deck.__init__` accepts `agents: Sequence[Agent]` and
`workflows: Sequence[NativeDefinition]`, both as objects.
`docs-site/content/build-your-deck/workflows.mdx:79` says invoke takes "an agent,
a `@tool`/`@workflow`, or an `AgentInstance`", which reads as accepting the
object.

**Problem**

The catalog is built from objects; one of the two object kinds cannot then be used
to address what it built. The asymmetry has a reason (the log records an invocable
by name, and identity has to be checked), but that reason applies equally to
`NativeDefinition`, which *is* accepted and *is* identity-checked at line 306.
The same check is simply not written for `Agent`.

**Why it matters**

It breaks the natural way to write a workflow, it broke the README, and it makes
`Deck(agents=[a])` + `ctx.invoke(a, ...)` fail while `Deck(workflows=[w])` +
`ctx.invoke(w, ...)` works. A user cannot predict which.

**Example scenario**

A user defines `triage = Agent(name="triage", ...)`, adds it to the deck, and
writes `await ctx.invoke(triage, ticket)`. They get an error suggesting
`ctx.agents`, a minting API for agents the catalog does *not* hold, which is the
opposite of their situation.

**Recommended direction**

Add an `Agent` branch to `_invoked_name` mirroring the `NativeDefinition` one:
resolve by identity against `deck._agents`, raise the same "this deck does not
hold it" message otherwise.

**Scope:** Local.

---

### [AD-03] `await deck.run(<workflow>)` returns `None` for a cancelled or paused run

**Severity:** High **Area:** API / Runtime **Confidence:** High

**Evidence**

`agentdeck/deck.py:1226-1229`:

```python
if isinstance(root, Agent):
    return await _turn_result(events)     # raises if no run.completed
result, _ = await _workflow_result(events)
return result                             # None if no terminal event
```

`_workflow_result` (line 235) initialises `result = None` and only assigns on
`RunInterrupted` or `RunCompleted`. It computes an `applied` flag for exactly this
purpose and `Deck.run` discards it with `_`.

Verified:

```
A) deck.run() on cancelled workflow returned: None   status: cancelled
B) deck.run() on paused workflow returned:    None   status: paused
```

The agent branch, on the same method, raises
`RuntimeError("the run ended without completing (paused or cancelled)")`.

**Problem**

The single most-used public method returns a value indistinguishable from a
workflow that legitimately returned `None`, for two outcomes that are not success.
The information needed to raise is computed on the line above and thrown away.

**Why it matters**

An operator cancels a run. The calling code sees `None`, takes its `None` branch,
and reports whatever that branch reports. There is no exception, no log line at
the call site, and no way to tell it apart from a real result. This is the classic
silent-failure shape, on the primary API.

**Example scenario**

```python
report = await deck.run("nightly_reconcile", batch)
store.save(report)          # saves None for every cancelled run
```

**Recommended direction**

Use the `applied` flag already returned: raise the same
`RunSuspendedError`/`RunStateError` shape `Run.__await__` raises for
`PAUSED`/`CANCELLED`, so `deck.run` and `await run` agree.

**Scope:** Local.

---

### [AD-04] Closing a Deck orphans a parked workflow

**Severity:** High **Area:** Runtime / Lifecycle **Confidence:** High

**Evidence**

`Deck.aclose` (`deck.py:890`) documents: *"A workflow waiting for an answer nobody
will now give is over when the deck that started it is."* The log disagrees.

`NativeExecutor.aclose` (`native/executor.py:155`) cancels the parked coroutine.
Nothing writes a terminal event for its run. `EventStorePort.claim_start`
(`core/ports/store.py:162`) states that neither `stale_after` nor a dead lease
applies to `PAUSED` or `WAITING_ANSWER`: *"A parked run holds its session until
something acts on it ... Held forever if nobody ever does either."*

Verified end to end, sqlite store, two sequential Decks:

```
before close: waiting_answer
deck closed
after reopen: waiting_answer
pending:      True
answer raised: ConfigError run '...' was suspended by a native workflow whose
               body this process no longer holds
final status: failed
```

**Problem**

Four separate consequences, in order of severity:

1. The run stays `WAITING_ANSWER` in the durable log with no owner, holding its
   session against every future turn, with no timer that will ever free it.
2. `run.pending()` still returns an interrupt, so an approval inbox lists it.
3. Answering it lands the resume claim (`run.resumed` is written) and *then* fails
   in the executor, flipping the run to `FAILED` and consuming the answer.
4. The failure is a `ConfigError`, a configuration error, for what is a recovery
   condition nobody misconfigured.

**Why it matters**

Human-in-the-loop approval is a headline capability. The failure mode is: deploy,
a workflow parks on an approval, the process restarts, the session is dead, the
inbox shows a live-looking item, and an operator clicking approve destroys the
run and loses their answer. Nothing in the log says why.

**Example scenario**

A refund workflow parks on `ctx.ask("approve $4,200 refund?", options=[True,
False])`. A rolling deploy restarts the worker. The customer's session is now
permanently unusable, and the finance operator's approval sends the run to
`FAILED`.

**Recommended direction**

`Deck.aclose` should seal the runs whose bodies it is about to destroy: write
`run.cancelled` with a reason naming the deck close, before `NativeExecutor.aclose`
cancels them. That frees the session, empties the inbox, and makes the log tell
the truth. The wider question (durable replay of imperative workflows) is
`AD-A2` in the architectural list and is not required for this.

**Scope:** Multi-module (`deck.py` ordering plus a `NativeExecutor` hook).

---

### [AD-05] `MCPLifecycle` never clears its registry across Decks

**Severity:** High **Area:** Architecture / Runtime **Confidence:** High

**Evidence**

`adapters/tools/mcp/lifecycle.py`. All state is class-level: `_servers`,
`_failed`, `_connected`, `_config`.

`shutdown()` (line 133) discards from `_connected` only. `_servers`, `_failed`
and `_config` survive.

`configure()` (line 98) skips any name already in `_servers` or `_failed`.

`reset()` (line 169) clears everything and is documented "Tests only".

**Problem**

`Deck`'s own docstring says sequential Decks are supported: *"Close one, construct
the next."* After the first `aclose()`:

- Deck 2's `configure()` skips every server name deck 1 registered, so deck 2 runs
  against deck 1's `.mcp.json`, not its own.
- `startup()` calls `connect()` on `MCPServer` objects that `shutdown()` already
  called `cleanup()` on.
- A server that failed to connect for deck 1 is in `_failed` permanently, so deck
  2 boots without it and logs nothing about why.

**Why it matters**

This is the mechanism behind the one-Deck-per-process restriction, and it is also
broken in the sequential case that restriction explicitly permits. The blast
radius is silent capability loss: agents boot with the MCP status banner telling
the model its tools are unavailable, and the operator has no signal.

**Recommended direction**

Make `shutdown()` clear `_servers`, `_failed` and `_config` alongside
`_connected`, and delete `reset()`. Longer term this is state that belongs to a
`Deck` instance, not to a class (see `AD-A1`).

**Scope:** Local for the clear, Architectural for the ownership.

---

### [AD-06] `await run` raises bare `RuntimeError`, outside the documented taxonomy

**Severity:** High **Area:** API / Types **Confidence:** High

**Evidence**

`core/errors.py` opens: *"One exception hierarchy: the errors the harness raises
are `AgentdeckError`s. Lets consumers write a single `except AgentdeckError`."*

Against that:

| site | raises | file |
|---|---|---|
| `Run._result`, run failed | `RuntimeError` | `deck.py:1592` |
| `Run._result`, run cancelled | `RuntimeError` | `deck.py:1594` |
| `Run._result`, unrecognised terminal kind | `RuntimeError` | `deck.py:1600` |
| `_turn_result`, no `run.completed` | `RuntimeError` | `deck.py:228` |
| `Runs.get`, bad argument combination | `ValueError` | `deck.py:1671` |
| `WorkflowCtx.agents` / `invoke` / `ask` with no executor | `RuntimeError` | `core/context.py:346, 438, 469` |
| `ctx.ask("")` | `ValueError` | `core/context.py:479` |
| `resolve_event_store` / `resolve_control_port` / `resolve_lease_port`, unknown scheme | `ValueError` | `composition.py:167, 198, 251` |
| missing `[redis]` / `[postgres]` extra | `ImportError` | `composition.py:237, 246` |
| `GatewayError` | separate root, own enum | `bindings/gateway.py:58` |

The quickstart documents the `RuntimeError` as expected output:
*"The run then surfaces as `RuntimeError: run '<run-id>' failed: ...`"*.

**Problem**

Four disjoint exception families reachable from one public API. A caller wrapping
`except AgentdeckError` around `await run` catches nothing when the run fails,
which is the single most likely thing to happen.

**Why it matters**

The taxonomy is good, carefully designed, and thoroughly documented. It is also
not what the most common failure path raises, so it does not deliver the one
benefit it was built for.

**Recommended direction**

Add `RunFailedError(AgentdeckError)` and `RunCancelledError(AgentdeckError)`
carrying the payload, and raise those from `Run._result` and `_turn_result`. Move
`Runs.get`'s and `ctx.ask`'s `ValueError` to `InputError`, and the `resolve_*`
scheme errors to `ConfigError`. Leave `GatewayError` alone: an SPI boundary
earning its own root is defensible, and it should be *documented* as a decision
rather than fixed.

**Scope:** Multi-module.

---

### [AD-07] A workflow returning a delegated agent's result fails with a raw pydantic error

**Severity:** High **Area:** API / DX **Confidence:** High

**Evidence**

```python
@workflow
async def handle(ctx: WorkflowCtx, q: str) -> str:
    return await ctx.invoke("SupportBot", q)
```

```
ValidationError 1 validation error for DataBlock
data  input was not a valid JSON value
      [type=invalid-json-value, input_value=TurnResult(output='ok', ...)]
```

`native/executor.py:299` `_as_output` wraps any non-list return in
`DataBlock(data=result)`, and `DataBlock.data` is `JsonData`.

**Problem**

`await ctx.invoke(<agent>)` resolves to a `TurnResult`, `await ctx.invoke(<tool>)`
resolves to a plain value, and the difference is invisible in the type system
because `invoke` is `-> Any` (AD-17). The natural composition therefore fails, at
the terminal event, with an internal type name and no guidance.

The docs get this right (`workflows.mdx:86` has the table). The error does not,
and the project's own standard is explicit: *"Every error must state what
happened, why it happened, and the exact code/action to resolve it."*

**Why it matters**

Delegating to an agent from a workflow and returning its answer is the most
obvious thing to write in this framework. It fails, late, with a pydantic
traceback pointing at a class the user has never heard of.

**Recommended direction**

Two options, and the first is better. (a) Have `_as_output` detect `TurnResult`
and raise an `InputError` naming `.output`. (b) Have it unwrap `TurnResult`
automatically, which is convenient but hides the agent/tool distinction the design
deliberately keeps.

**Scope:** Local.

---

### [AD-08] Resuming a paused agent turn re-executes its tools, undocumented

**Severity:** High **Area:** Docs / Runtime **Confidence:** High

**Evidence**

`Runtime.resume_run` docstring (`service.py:377`): *"The engine is re-entered
rather than un-suspended ... a step the paused turn already took can be taken
twice, so a tool with side effects has to tolerate being called again."*

`tests/test_run_control.py:426` proves it: `assert seen == [calendar, calendar]`.

`docs-site/content/runs-and-control/pause-resume.mdx` is 17 lines, one code fence,
and contains none of the words *replay*, *twice*, *again*, *idempotent* or *side
effect*. Neither does any other page under `runs-and-control/` or
`build-your-deck/`.

**Problem**

The behavior is known, deliberate, correct for the architecture, and tested. It is
also the highest-consequence thing a user can trip over, and the page named after
the feature does not mention it.

**Why it matters**

`await run.pause()` and `await run.resume()` read as symmetric and inert. They are
not: for an agent target, resume replays the turn from `run.started` with the log
as history, so every tool call the paused turn made is made again. A tool that
charges a card, sends an email or provisions infrastructure will do it twice.

**Example scenario**

An operator pauses a running agent to inspect it, sees nothing alarming, and
resumes. The agent had already called `issue_refund`. It calls it again.

**Recommended direction**

Documentation, not code. Add the executor-by-executor table that
`Runtime.signal`'s docstring already contains to `pause-resume.mdx`, and state the
tool-idempotency requirement in `build-your-deck/tools.mdx`. Consider surfacing it
on `Run.pause`'s own docstring, which currently says only "recorded, not stopped".

**Scope:** Local.

---

### [AD-09] `Agent.run()` is a second execution path that bypasses the runtime

**Severity:** High **Area:** Architecture / API **Confidence:** High

**Evidence**

`authoring/agent.py:156-171`:

```python
def build(self) -> Any:            # returns an agents.Agent
async def run(self, message=None, **runner_options) -> Any:   # returns SDK RunResult
```

Backed by `authoring/runners/agent.py` (167 lines): `BaseRunner`, `HeadlessRunner`,
`StreamDone`. Callers: `Agent.run()` and one parity test. `HeadlessRunner.run_streamed`
and `StreamDone` have zero callers anywhere.

`TurnResult`'s own docstring (`deck.py:134`) states the design rule this violates:
*"never the SDK's own result object, so a caller depends on agentdeck's event
schema rather than on whichever engine ran the turn."*

**Problem**

A public method on a root-exported class runs an agent with no run id, no event
log, no control plane, no observers, no session claim, no delegation accounting,
no context injection wiring and no error taxonomy, and hands back the underlying
SDK's object. Its module docstring cites `AgentNode`, `BaseSandboxAgent` and
`agentdeck.observability`, none of which exist.

`BaseRunner` also has exactly one subclass, which the project's own standards
name as a smell.

**Why it matters**

Every guarantee in the product's pitch is absent from this path, and a user
reaching for it (it is the shortest thing that looks like "run this agent") gets
none of them. It is also the only public API that returns a third-party type.

**Recommended direction**

Delete `Agent.run()` and `agentdeck/authoring/runners/`. `Agent.build()` is
defensible as an escape hatch for handing an agent to the SDK directly, but should
be typed and documented as such. If a no-log one-shot is genuinely wanted, it
should be `deck.run(..., persist=False)`, not a parallel stack.

**Scope:** Multi-module.

---

### [AD-10] `Deck.aclose()` leaks sessions, executors and the store if `drain()` raises

**Severity:** Medium **Area:** Runtime / Lifecycle **Confidence:** High

**Evidence**

`deck.py:952-970`:

```python
try:
    if self._runtime is not None:
        await self._runtime.drain()
    if self._sessions is not None:
        await self._sessions.aclose()
    for executor in self._executor_instances or ():
        await executor.aclose()
    if self._owns_store and self._runtime is not None:
        await _aclose_store(self._runtime.store)
finally:
    if self._started_mcp: ...
    self._state = "CLOSED"
```

**Problem**

Four sequential closes in one `try`. A failure in the first skips the other three.
`__aenter__`'s rollback path immediately above (line 856) is careful about exactly
this, wrapping each step and logging: the two paths disagree about how much care
teardown deserves.

**Why it matters**

`Runtime.drain` already gathers with `return_exceptions=True`, so the realistic
raiser is a cancellation arriving during shutdown. The result is a leaked session
store connection, a leaked thread pool, live parked coroutines, and an open sqlite
handle. In a long-lived process running sequential Decks that accumulates.

**Recommended direction**

Give each of the four its own try/except-log, matching `__aenter__`'s rollback
style, and re-raise the first at the end.

**Scope:** Local.

---

### [AD-11] `serve()` binds `0.0.0.0` by default

**Severity:** Medium **Area:** API / DX **Confidence:** High

**Evidence** `deck.py:690`, `deck.py:707`, `bindings/exposure.py:296`: all three
default `host: str = "0.0.0.0"`.

**Problem** The one-liner in the docs (`deck.serve(Native.http())`) exposes the
deck on every interface. Nothing in the signature, the docstring or the log says
so.

**Why it matters** A developer running the quickstart on a laptop on a conference
network is serving their agent to that network. `uvicorn`'s own default is
`127.0.0.1` for this reason.

**Recommended direction** Default to `127.0.0.1` and log one line naming
`host="0.0.0.0"` as the deployment setting. This is a breaking change to a
default, so it belongs at a major.

**Scope:** Local.

---

### [AD-12] Cancel cascades only inside one process, but is documented unconditionally

**Severity:** Medium **Area:** Runtime / Docs **Confidence:** High

**Evidence** `Runtime.signal` (`service.py:475`):

```python
for child in [c for c, placed in self._tree.items() if placed.parent == run_id]:
    await self.signal(child, verb, reason, namespace=namespace)
```

`self._tree` is `dict[str, _Delegation]`, populated by `delegate()` while a run is
being played in *this* Runtime. The docstring says: *"A cancel cascades to the
runs this one delegated, and theirs in turn. A parent that stops while a child
keeps burning tokens is worse than not offering cancel at all."*

The edge is durable (`RunStarted.parent_run_id`); the traversal is not.

**Problem** Two workers sharing a store, a parent on worker A and a child on
worker B: cancelling the parent leaves the child running. The store knows the
edge. Nothing reads it.

**Why it matters** The stated reason for the feature ("a child that keeps burning
tokens") is exactly the multi-worker case. The docstring makes a promise the
single-process implementation cannot keep, with no line marking the limit.

**Recommended direction** Short term, state the limit in the docstring and in
`runs-and-control/lifecycle-and-control.mdx`. Longer term, resolve children from
the store by `parent_run_id` when the tree has no entry, which is the same
fallback `_inherited` already does for sessions one line away.

**Scope:** Local for the doc, Multi-module for the fallback.

---

### [AD-13] `Run.status()` uses `assert` for a public-API invariant

**Severity:** Medium **Area:** Types / Runtime **Confidence:** Medium

**Evidence** `deck.py:1429-1436`:

```python
if status is None:
    opening = self._deck._executions.get(self.id)
    assert opening is not None and not opening[1].done(), (
        f"a Run handle always names a run that exists: {self.id!r}"
    )
    status = RunStatus.RUNNING
```

**Problem** Two issues. The assertion is stripped under `python -O`, after which
`status` is silently `RunStatus.RUNNING` for a run that has no events at all. And
it is reachable: `_invoke` returns a child handle before the opening claim lands,
so a store failure inside that task pops `_executions` (via `_execution_done`)
before `run.started` is written. `Run.status()` then raises `AssertionError`
instead of the store's own error.

`Run._result` sits directly on top of this and loops on `status()`.

**Why it matters** `assert` on a public method turns a diagnosable store failure
into an `AssertionError` with no cause chain, and turns into a wrong answer under
optimisation. This is the only `assert` in the tree guarding a runtime condition
rather than a type-checker hint.

**Recommended direction** Replace with an explicit raise carrying the execution
task's exception when there is one.

**Scope:** Local.

---

### [AD-14] `Executor.suspendable` is a capability flag nothing sets to `False`

**Severity:** Medium **Area:** Architecture / API **Confidence:** High

**Evidence** All three executors declare `suspendable: ClassVar[bool] = True`
(`native`, `openai_agents`, `stub`). It drives `Runtime.suspends()`,
`core.status.can_of()`, `Run.can`, and the `UnsupportedControlError` branch in
`Run._admits`.

For recovered handles it is not read at all: `deck.py:1349`
`_RECOVERED_SUSPENDABLE = True`, with the project's own `ponytail:` marker
acknowledging the guess.

**Problem** A capability-negotiation mechanism spanning four modules, with one
possible value, plus a hardcoded assumption for the case it cannot answer. `run.can`
therefore reports pause and resume as available on every run in every state where
the state table allows it, whether or not that is true.

**Why it matters** This is speculative generality, and it is not free: it is a
public concept (`run.can`), a port member, a lifecycle branch, and a documented
error type, all serving a distinction that does not currently exist.

**Recommended direction** Leave it. The mechanism is small, the code already
marks it as a known shortcut, and removing a public `run.can` is harder than
keeping it. Note it in the debt ledger, and revisit when the first non-suspending
executor lands (the project tracks this as `#337`).

**Scope:** Architectural.

---

### [AD-15] `ToolSourcePort` is a port with one implementation and zero production callers

**Severity:** Medium **Area:** Architecture **Confidence:** High

**Evidence** `core/ports/tools.py` (57 lines) defines `ToolSet` and
`ToolSourcePort`, both exported from `agentdeck.core.ports.__all__`.
`adapters/tools/mcp/source.py` (47 lines) is the sole implementation.

The production path does not use it: `authoring/compile.py:295` calls
`resolve_agent_mcp_status()` from `adapters/tools/mcp/wiring.py` directly.

`MCPToolSource` appears in exactly one place outside its own module:
`tests/test_mcp_tool_source.py` (172 lines), including
`test_the_mcp_source_is_a_tool_source_port`, which asserts the port relationship
and nothing else.

`docs/engineering/architecture.md` §2: *"Do not add a port where no substitution
boundary exists. A port with one implementation is a port that has not earned
itself."*

**Problem** ~276 lines of port, implementation and test that no production code
path reaches, held alive by a test asserting its own existence.

**Why it matters** It is not harmful, but it is exactly the shape the project's
standards forbid, sitting inside `core/`, which has the strictest import rules and
should be the most carefully curated ring.

**Recommended direction** Either route `compile_agent` through the port (making it
real) or delete the port, the adapter and the test. `resolve_agent_mcp_servers`
in the same package is also dead and goes with either choice.

**Scope:** Multi-module.

---

### [AD-16] `agentdeck/runtime/capture.py` is entirely dead

**Severity:** Low **Area:** Architecture **Confidence:** High

**Evidence** 32 lines defining `Capture` and `CaptureActor`. Zero references
anywhere in `agentdeck/`, `tests/`, `examples/`, `scripts/` or `docs/`. 0%
coverage. Its docstring cites an import from `agentdecks_core`, a package the
docstring itself calls "never-extracted".

**Problem** Dead code in the runtime ring, against a standard that says "Delete
dead code aggressively".

**Recommended direction** Delete the module.

**Scope:** Local.

---

### [AD-17] The authoring surface is typed `-> Any`

**Severity:** Medium **Area:** Types **Confidence:** High

**Evidence**

| symbol | annotation | file |
|---|---|---|
| `tool()` | `-> Any` | `authoring/native.py:61` |
| `workflow()` | `-> Any` | `authoring/native.py:76` |
| `WorkflowCtx.invoke()` | `-> Any` | `core/context.py:353` |
| `WorkflowCtx.parallel()` | `-> list[Any]` | `core/context.py:372` |
| `WorkflowCtx.ask()` | `-> Any` | `core/context.py:445` |
| `Deck.run()` | `-> TurnResult \| Any` | `deck.py:1198` |
| `Run.__await__()` | `-> Generator[Any, None, Any]` | `deck.py:1543` |
| `Agent.build()` / `Agent.run()` | `-> Any` | `authoring/agent.py:156, 167` |

`pyproject.toml` declares `Typing :: Typed`. `CLAUDE.md` §3: *"Strict annotations
everywhere. No unprincipled `Any`."*

**Problem** `@tool`/`@workflow` erase the decorated function's type entirely, so
`Deck(workflows=[w])`'s declared `Sequence[NativeDefinition]` is never actually
checked. `TurnResult | Any` collapses to `Any`. The four `WorkflowCtx` members are
`Any` because `core/` may not import `agentdeck.deck.Run`, which is a real
constraint honestly documented at `core/context.py:258`.

**Why it matters** This is the direct cause of AD-01 and AD-07 going unnoticed:
returning a `TurnResult` from a function annotated `-> str` is invisible to `ty`
because `invoke` returns `Any`.

**Recommended direction** Three separable pieces, easiest first. (a) Give `tool`
and `workflow` `@overload`s returning `NativeDefinition`. (b) Change
`Deck.run` to `-> Any` and be honest, or split `run_agent`/`run_workflow`. (c) The
`Run` type in core is genuinely blocked by the ring rule; a `Protocol` in
`core/context.py` describing the handle (`ChildRun` already exists and is
`runtime_checkable`) could carry `__await__` and be the return type.

**Scope:** Multi-module for (a) and (b), Architectural for (c).

---

### [AD-18] Shipped observers block the event loop, defeating the dispatch design

**Severity:** Medium **Area:** Concurrency **Confidence:** High

**Evidence** `observers.py`:

```python
class FileObserver(Observer):
    async def emit(self, event):
        with self._path.open("a", encoding="utf-8") as f:   # blocking open+write+close
            f.write(event.model_dump_json()); f.write("\n")

class ConsoleObserver(Observer):
    async def emit(self, event):
        print(...)                                           # blocking write to stdout
```

`runtime/dispatch.py` is 454 lines and 9 tuning constants built on the premise
that *"a slow `emit` costs this sink's own backlog and nothing else"*.

**Problem** `SinkDispatch._consume` awaits `emit` on the run's own event loop. A
synchronous `open`/`write`/`close` per event, or a `print` into a full pipe, blocks
that loop, and with it every run, every store append and the lease renewer
(`service.py:698` already flags the renewer's sensitivity to loop stalls).

**Why it matters** The one mechanism the project invested most heavily in
protecting is undermined by the two simplest sinks it ships. `EMIT_TIMEOUT` does
not help: `asyncio.timeout` cannot fire while the loop is blocked.

**Example scenario** `FileObserver("/mnt/nfs/audit.jsonl")` on a stalled NFS
mount stops every run in the process, not just the audit trail.

**Recommended direction** Wrap both bodies in `asyncio.to_thread`, or keep one
open file handle and write through a thread. Note the constraint in `Observer`'s
port docstring, which today says "Buffer slow work internally" but not "do not
block the loop".

**Scope:** Local.

---

### [AD-19] Settings are a process-global cache, so the composition root does what it forbids adapters from doing

**Severity:** Medium **Area:** Architecture **Confidence:** High

**Evidence** `runtime/settings.py:501`: `@lru_cache(maxsize=1)` on `get_settings()`,
which also calls `load_dotenv(...)` (a mutation of `os.environ`) on first use.

`composition.py:9-13`: *"an executor that reached for `get_settings()` itself could
not be handed a different endpoint by a caller, and a second front door would have
to mutate process state to get one."*

`Deck` has no `settings=` parameter. `Deck.__aenter__` calls
`resolve_run_settings()`, `resolve_event_store()`, `resolve_control_port()` and
`resolve_observers()`, all of which read the global. `Deck.settings` returns
`get_settings()`.

**Problem** The stated principle is right and is applied one ring too shallow. The
composition root is exactly as unable to be handed a different endpoint as the
executor the docstring warns about. Tests work around it with
`reset_settings_cache()`, referenced 52 times across the suite.

**Why it matters** Two Decks with different model endpoints, two tenants with
different Langfuse projects, or a library embedding AgentDeck twice are all
impossible without mutating process state. It compounds with the one-Deck-per-
process rule (AD-05) to make the whole system a process-scoped singleton.

**Recommended direction** Add `Deck(settings=Settings | None)` threaded to the
`resolve_*` calls, which already all accept an optional settings argument. That is
close to a one-line change per call site and removes the last global that has a
sensible owner.

**Scope:** Multi-module.

---

### [AD-20] `agentdeck runs signal <id> resume` silently does nothing for a paused run

**Severity:** Medium **Area:** DX **Confidence:** High

**Evidence** `cli.py` module docstring: *"A recorded `resume` here only lifts a
pause that has not landed yet: continuing a run that already stopped means playing
it on, which needs the event log."* The `resume` choice is still offered
(`choices=[sig.value for sig in Signal]`).

`core/status.py:_PAUSED_ROW["resume"]` is `Action.PROCEED, why="the pause this
resume lifts had already been lifted"`, read only by a claim that something else
has to initiate.

**Problem** The obvious use of the command (a run is paused, resume it from
another terminal) is the one case it cannot serve, and it exits 0.

**Why it matters** A CLI verb that succeeds and does nothing is worse than one
that does not exist. The operator watches `run.status()` stay `paused` with no
error to search for.

**Recommended direction** Drop `resume` from the CLI's choices and print the
`deck.runs.get(id).resume()` line instead, or make the command refuse a run whose
status is already `PAUSED` with a message naming what does work.

**Scope:** Local.

---

### [AD-21] `run.pause()` on a `WAITING_ANSWER` run vetoes the answer rather than pausing

**Severity:** Medium **Area:** API / Docs **Confidence:** High

**Evidence** `core/status.py`, `WAITING_ANSWER` row:

```python
Operation.PAUSE: _STOPPABLE,   # LEGAL
```

with the comment *"it is not a pause of the running work. It is the veto
`_WAITING_ANSWER_ROW` honours: the answer is held back until somebody lifts it."*
`_WAITING_ANSWER_ROW["pause"]` is `Action.REFUSE`, and `Runtime.resume` raises
`RunStateError` for it.

`Run.pause`'s docstring says only: *"Ask the run to stop at its next safe point,
and record why."*

**Problem** One verb, two different meanings depending on state, and the second is
documented only in the internal lifecycle table.

**Why it matters** A UI wiring a Pause button to `run.pause()` will, on a run
awaiting approval, silently lock that approval out. The person who then clicks
Approve gets `RunStateError` with no memory of anyone pausing anything.

**Recommended direction** Documentation, plus one sentence on `Run.pause`. The
semantics themselves are defensible and the table is the right place for the rule.

**Scope:** Local.

---

### [AD-22] Three event kinds have no producer, but have consumers and tests

**Severity:** Low **Area:** Architecture **Confidence:** High

**Evidence** `thought.delta`, `artifact.created` and `input.appended` are defined
in `core/events.py` and emitted by nothing in `agentdeck/`. `InputAppended`'s own
docstring says *"No producer yet."*

They are nonetheless handled in `adapters/bindings/agui/adapter.py` (`_thought_delta`,
`_artifact_created`), classified in `views.py`, and exercised in
`tests/bindings/test_agui_binding.py` and `tests/test_views.py`.

`ControlVerb` also carries `"steer"`, and `SafePoint` carries `node_boundary`,
both with no producer.

**Problem** Speculative surface with real consumer code and real tests behind it.

**Why it matters** Low, and there is a genuine counter-argument the code makes
well: a closed `Literal` cannot gain a member additively for a *reader*, so
`ControlVerb` and `SafePoint` are correctly complete at birth. That reasoning does
not extend to the three payload classes, which `UnknownEvent` already handles
additively by design.

**Recommended direction** Leave `ControlVerb` and `SafePoint`. For the three
payload classes, decide: either a producer lands or they go, and the AG-UI
adapter's branches go with them. Not urgent.

**Scope:** Local.

---

### [AD-23] Prose is 32% of source, and carries the project's change history

**Severity:** Medium **Area:** Code quality **Confidence:** High

**Evidence** Measured across `agentdeck/`:

```
16,590 total lines
 4,385 docstring lines
   889 comment lines
       -> 31.8% prose
```

Worst offenders: `core/ports/store.py` 72%, `core/context.py` 51%,
`runtime/service.py` 44%, `deck.py` 38%.

Much of it is change history. A sample from `deck.py` alone: `(#636)`, `(#451)`,
`(#176)`, `(#213)`, `(#617)`, `(#412)`, `(#181)`, `(#162)`, `(#572)`, `(#488)`,
`(#337)`, `(#391)`, `(#419)`, `(#421)`, `(#471)`, `(#487)`, `(#680)`, `(#491)`.

`CLAUDE.md` §3: *"Comments: Extremely rare, max 1-2 lines explaining non-obvious
why."* A 41KB `scripts/slopcheck.py` with 12 rules exists to enforce this.

**Problem** The prose is high quality, and that is the trap. It is where the rules
actually live, so it cannot be deleted, so it grows. Several rulings exist *only*
in a docstring: the ownership rule for closing a store, the reason `_events` stops
at a segment boundary while `_result` does not, the per-executor pause semantics
table.

**Why it matters** Three concrete costs. Reading `deck.py` means reading 650 lines
of prose to find 1,072 lines of code. Prose goes stale silently (this audit found
five removed symbols still cited, AD-25). And a rule written only in a docstring is
not enforced by anything.

**Why it is not simply "delete comments"** Half of these earn their place by the
project's own test: they name a constraint the code cannot show. The problem is
volume and location, not existence.

**Recommended direction** Move the *rulings* to `docs/design/` where they can be
linked and reviewed as a set, and leave one-line pointers. Move the *history*
(`#NNN` narratives) out entirely: git and the CHANGELOG already hold it, and it is
the part most likely to go stale. Consider a slopcheck rule capping the
prose-to-code ratio per file, which is a number the project already computes for
`SLOP012`.

**Scope:** Multi-module.

---

### [AD-24] Five ways to start a run, four ways to read one

**Severity:** Medium **Area:** API **Confidence:** High

**Evidence**

Starting: `await deck.run(name, input)`, `deck.stream(name, input)`,
`await deck.runs.start(name, input)`, `ctx.invoke(target, ...)`,
`await agent.run(message)`.

Reading: `deck.stream(...)`, `run.events(follow=True)`, `run.events(follow=False)`,
`await run`.

`run` and `stream` differ from `runs.start` only in whether they await the drain
task and what they hand back; all three call `_start`. `deck.stream(name, input)`
and `runs.start(...)` then `run.events(follow=True)` produce the same events for
the same run.

**Problem** The philosophy calls for "one obvious path first". There are three
equally documented ones, and their differences are behavioral rather than nominal:
`deck.run` awaits the whole task, `deck.stream` awaits it *after* the events,
`runs.start` never does.

**Why it matters** The quickstart teaches `runs.start` + `events(follow=True)` +
`await run`, three concepts, where `await deck.run(...)` is the stated one obvious
path. A reader's first impression of the API is that it has many doors.

**Recommended direction** Do not consolidate the runtime paths; the distinctions
are real. Consolidate the *teaching*: make `deck.run` the quickstart, and present
`runs.start` under "when you need a handle". Delete `Agent.run` (AD-09), which is
the only one of the five that is not a variation of one model.

**Scope:** Local (docs), Multi-module (deleting `Agent.run`).

---

### [AD-25] In-code references to five removed symbols

**Severity:** Low **Area:** Docs **Confidence:** High

**Evidence**

| stale reference | cited in |
|---|---|
| `App` (removed composition root) | `composition.py:3`, `runtime/service.py:191`, `core/ports/tools.py:6`, `adapters/tools/mcp/lifecycle.py:4,14` |
| `agentdeck/surfaces/`, `serve.py` | `CLAUDE.md` §2, `cli.py` docstring, `docs/delivery/*` |
| `agentdeck.observability.Langfuse` | `authoring/runners/agent.py:10` |
| `AgentNode`, `authoring/nodes.py` | `authoring/runners/agent.py:3` |
| `BaseSandboxAgent` | `authoring/runners/agent.py:6` |
| `agentdecks_core` | `runtime/capture.py:4` |
| "10 contracts" (there are 12) | `docs/engineering/architecture.md:5` |

**Problem** `CLAUDE.md` is the file every coding agent and new contributor reads
first, and its architecture section names a package that does not exist.

**Why it matters** Small individually. Together they are the measurable form of
AD-23: prose that outlived what it described, in a codebase that depends on prose.

**Recommended direction** One sweep. Most of it disappears if AD-09 and AD-16 are
actioned.

**Scope:** Local.

---

### [AD-26] Exact pins on `openai-agents` and `openai` in base dependencies

**Severity:** Medium **Area:** Other **Confidence:** High

**Evidence** `pyproject.toml`:

```toml
"openai-agents==0.17.0",
"openai==2.32.0",     # pinned pair
```

**Problem** A library, not an application, pinning `openai` exactly. Any consumer
that also uses `openai` directly, or depends on anything that does, is now locked
to 2.32.0 or has an unsolvable resolution.

**Why it matters** This is the most likely reason a prospective user cannot
install AgentDeck alongside what they already have. The reason for the pin is
sound and documented (an upstream incompatibility); the mechanism is too strong
for a library.

**Recommended direction** `openai>=2.30,<2.33` and `openai-agents>=0.17,<0.18`,
with `uv.lock` continuing to pin the exact pair for CI. That preserves the
guarantee for this repo without exporting it to every consumer.

**Scope:** Local.

---

### [AD-27] `SinkDispatch` is 454 lines and 9 constants for three shipped sinks

**Severity:** Low **Area:** Architecture **Confidence:** Medium

**Evidence** `runtime/dispatch.py`: bounded queue, oldest-drop eviction, failure
streak breaker, cooldown, single-event probe, dual log throttling (streak-based
and window-based), separate timeouts for emit, flush, close and reap, and drop
accounting. Nine module constants, none exposed as a setting.

Shipped observers: `ConsoleObserver` (a `print`), `FileObserver` (an append),
`LangfuseObserver` (which buffers internally anyway).

**Problem** The machinery is more sophisticated than anything it currently
protects against, and two of the three sinks defeat it by blocking the loop
(AD-18).

**Why it matters, and why it is Low** The invariant it enforces (a run is never
charged for its slowest reader) is genuinely important and correct, and users
supply their own `Observer`s, which is where an HTTP-backed sink actually appears.
The code is also excellent: every branch is reasoned and tested. This is noted as
a proportionality observation, not a defect.

**Recommended direction** Leave it. Fix AD-18 so the guarantee is real, and
consider whether `QUEUE_CAPACITY` and `EMIT_TIMEOUT` should be `Deck` arguments
rather than constants.

**Scope:** Leave alone.

---

## 5. Architecture assessment

### What is right

**The log is the truth, and nothing else is.** Status is a fold over events, not a
column. `seq` and `ts` are stamped inside the write. There is no counter in the
process. Every store implements a conditional append for the two transitions that
matter. This single decision is why the crash, takeover, multi-worker and
restart stories are all coherent, and it is the reason the system is trustworthy.

**`core/status.py` is a model of how to write a state machine.** Four tables,
keyed exhaustively over the product of `RunStatus` and `Operation`, so a state
added without a rule raises `KeyError` at import. `TERMINAL_STATUSES`,
`SUSPENDED_KINDS` and `RESUMABLE_STATUSES` are all derived, never listed twice.
`decide()` is the only reader. Nothing else in the tree may write a lifecycle
rule, and nothing else does.

**The ring discipline is real and enforced.** 12 import-linter contracts, all
kept, including a plugin fixture that proves the SPI boundary from outside the
tree. `core/` really does import only stdlib and pydantic.

**Adapters are honest about their limits.** The Redis, Postgres and SQLite stores
each implement the same contract suite. The MCP transport handles reconnect. The
Langfuse observer states, in its docstring, exactly why the raw span layer cannot
nest and points at the issue.

### What has drifted

**The composition root grew a second job.** `composition.py` was built to be the
one place adapters are instantiated. `Deck.__aenter__` instantiates four of them
directly. Neither file is wrong on its own; together they mean "where is an
adapter built" has two answers.

**Three process-global singletons pin the whole system to one Deck.**
`_live_deck`, `MCPLifecycle`'s class state, and `get_settings`'s cache. The first
is a guard *for* the second. The third makes per-Deck configuration impossible.
None of the three has to be global: `MCPLifecycle` could be a `Deck` member and
`Settings` could be a constructor argument, and then `_live_deck` and the
one-Deck-per-process rule could both be deleted. That is the single largest
simplification available in the codebase.

**`Deck` has accumulated.** 1,722 lines holding: a catalog, a four-state
lifecycle, a process claim, an execution-task registry, a minted-agent registry, a
follow loop with its own polling constant, result shaping for two invocable kinds,
an `_Agents` mint API, a binding host, and three `serve` variants. `Run` and `Runs`
live in the same file for a documented and correct reason (circular imports), but
that reason does not extend to the rest.

**Two ways to run an agent.** `Agent.run()` / `HeadlessRunner` versus the
`Runtime` + `OpenAIAgentsExecutor` path. One has everything the product sells; the
other has none of it and is shorter to type.

### Accidental complexity, ranked

| what | cost | root cause |
|---|---|---|
| `reconcile.py` (199 lines) | keeping the SDK's session store and the event log in agreement | two sources of conversation state, one owned by the engine |
| `SinkDispatch` (454 lines) | breaker, probe, four timeouts | a real NFR, over-served |
| `_events` vs `_result` (two follow loops) | 30 lines of docstring explaining why they differ | segment boundaries are a leaked implementation concept |
| `_UNSET` sentinel x 12 in `Agent.__init__` | hand-rolled immutability against a standard that mandates frozen dataclasses | `base=` needs "omitted" distinct from "explicitly falsy" |
| `MCPLifecycle` class state | `_live_deck`, the one-Deck rule, AD-05 | a singleton where an instance would do |

---

## 6. Public API assessment

**The question asked: could a developer correctly predict how this API behaves
without reading its implementation?**

For most of it, yes. For five things, no, and four of those are findings above.

| API | predictable? | why not |
|---|---|---|
| `Deck(...)`, `build()`, `async with` | yes | |
| `deck.run(agent, ...)` | yes | returns `TurnResult`, raises on failure |
| `deck.run(workflow, ...)` | **no** | returns `None` for cancelled and paused (AD-03) |
| `deck.stream(...)` | yes | |
| `deck.runs.start/get/list` | yes | `get()`'s two-form argument rule is clear |
| `run.pause()` on a running run | yes | |
| `run.pause()` on a waiting run | **no** | it vetoes the answer (AD-21) |
| `run.resume()` on an agent | **no** | replays the turn and its tools (AD-08) |
| `await run` | mostly | raises `RunSuspendedError` (good), bare `RuntimeError` (AD-06) |
| `run.events(follow=)` | mostly | "ends at a segment boundary" is an internal concept in a public docstring |
| `ctx.invoke(<workflow object>)` | yes | |
| `ctx.invoke(<Agent object>)` | **no** | refused (AD-02) |
| `ctx.invoke(<agent name>)` result | **no** | `TurnResult`, and returning it from a workflow raises (AD-07) |
| `ctx.ask` / `run.answer` | yes | this pair is very well designed |
| `ctx.parallel` | yes | all-or-nothing is stated and the `WAITING_ANSWER` exception is stated |
| `Observer` / `views` | yes | |
| `deck.serve(...)` | mostly | `0.0.0.0` default (AD-11) |
| `Agent.run()` | **no** | bypasses everything (AD-09) |

### Naming and symmetry

Naming is consistent and good. `run`/`stream`/`start`, `pause`/`resume`/`cancel`,
`ask`/`answer`, `can.pause`/`pause()`. The decision to put every per-run verb on
`Run` and only `start`/`get`/`list` on `Runs` is right and is held to without
exception.

Asymmetries worth naming:

- `deck.agents` returns a `MappingProxyType`; `deck.workflows` builds and returns a
  fresh mutable `dict` on every access, while the class docstring says both are
  "read-only mappings".
- `deck.run` raises for one invocable kind and returns `None` for the other
  (AD-03).
- `ctx.invoke` accepts objects for one kind and names for the other (AD-02).
- `TurnResult` is a hand-written class with `__slots__`, `__eq__` and `__repr__`,
  and mutable attributes, where `CLAUDE.md` mandates
  `@dataclass(frozen=True, slots=True)` for internal immutable value objects.

### Things that should probably not be public

`Deck._release()` is a static method whose docstring says "The test suite's safety
net ... Not public API", shipped in the production class. `agentdeck.core.ports`
exports `ToolSourcePort` and `ToolSet` (AD-15). `agentdeck.testing` is correctly
public and is unusually good.

### Things the API gets notably right

The `key` / `run_id` separation (a caller-supplied identifier never becomes an
address) is the kind of decision that prevents a whole class of bug and is very
rare to see made correctly. `namespace` as an opaque key AgentDeck never parses,
with `None` as a first-class mode, is right. `run.can` as explicitly
non-authoritative, with the methods as the real answer, is right. `ctx.ask` with
`options` refused *before* the claim, so the run stays answerable, is excellent.

---

## 7. Execution and lifecycle assessment

### Sync versus async

One asyncio model throughout, with sync tool bodies dispatched to a shared
`SyncToolWorkers` pool. The `ToolCtx.safepoint()` refusal for THREAD bodies is
handled well: it is a plain `def` returning a coroutine, so the refusal is a real
exception at call time rather than an un-awaited coroutine. THREAD bodies get one
cancel-only checkpoint after the worker returns, which is the honest maximum.

Two loop-blocking paths remain: `FileObserver`/`ConsoleObserver` (AD-18), and the
lease renewer's documented inability to run while the loop is blocked (already
carries a `ponytail:` marker).

### Cancellation

Cooperative, three-phase, and the phases are recorded separately, which is the
right model and rare to see done. Every exit from `Runtime._play` seals the run:
terminal payload, suspension, `GeneratorExit`, `CancelledError`, and an engine
that just stops. The `CancelledError` arm exists specifically because it is a
`BaseException` and the `Exception` arm never saw it. `_close_cancelled` shields
the closing append. This is careful, correct work.

Gaps: the cascade is process-local (AD-12), and `Runtime._record` raises
`asyncio.CancelledError` as a control-flow signal for abandoned runs, which makes
the drain task report `cancelled()` for a run nobody cancelled, and `Run._result`
re-raises it to the caller.

### Shutdown

`Deck.aclose` cancels each in-flight run with two 1s attempts, then abandons it via
`Runtime.close_cancelled`, which marks it before its first await so no later append
can slip past. The reasoning is sound and the ordering is documented.

Three problems: the four-step teardown shares one `try` (AD-10), sinks are drained
*before* executors close so anything a closing executor records reaches no
observer, and parked workflows are cancelled without their runs being sealed
(AD-04).

### Partial failure

`__aenter__` handles its own rollback in one `try`, unwinding observers, the
runtime and the store, logging each failure without letting it mask the original.
`Exposure._lifecycle` does the same for bindings and the deck. Both are good. They
disagree with `aclose`, which does not.

### Resource ownership

The ownership rule ("configuration this Deck instantiated is its to close; an
object the caller handed in stays the caller's") is stated once and held to,
including the deliberate exception that every observer gets its `close()` even a
caller's own, because that call means "flush". `_aclose_store` duck-types
`aclose`/`close` because the port declares neither, which is the one place the
rule leaks.

### Race conditions found

None that are live and unhandled. The claim-based design closes the ones that
matter, and the contract suite exercises them across processes. Two narrow ones:
`Run.status()`'s assertion window (AD-13), and `_reporting.fire`'s `pending.pop()`
removing the rightmost entry rather than the one it appended, which is only wrong
if two threads append between the append and the failure, and only on a path
(loop already closed) where the report is being dropped anyway.

---

## 8. Test-gap analysis

Full matrix in `analysis/TEST_GAPS.md`. Summary:

The suite is strong where it is hardest to be strong: four-store contract
replay, multiprocess concurrency, crash reconciliation, killed-worker takeover
by both lease and timer, `seq` density under concurrent writers, and a golden
event-schema snapshot replayed in a second process. Documentation fences are
executed as real subprocesses against a scripted model server, which is a level
of docs testing most projects do not attempt.

The gaps cluster in one place: **what happens when a Deck shuts down while
something is unfinished.**

| gap | finding |
|---|---|
| `aclose()` with a parked `ctx.ask` workflow | AD-04 |
| `aclose()` where `drain()` raises | AD-10 |
| second Deck in one process with MCP configured | AD-05 |
| `deck.run()` return value for a cancelled or paused workflow | AD-03 |
| cross-process cancel cascade | AD-12 |
| `Run.status()` when a child's claim fails | AD-13 |
| README fences executed | AD-01 |

Every one of the seven is a finding above, and every one is cheap to test. That
correspondence is itself the useful signal: the defects this audit found are
exactly the ones the suite does not reach, and the suite does not reach them for
one structural reason. It tests `Runtime` heavily in isolation and `Deck`
heavily in the happy path, and the seams between them at teardown are tested
least.

---

## 9. Documentation consistency assessment

### The good

The docs-site content is accurate where it exists, and it is machine-checked
harder than most: `test_docs_site.py` parses every fence and pins nav keys to
pages, `test_docs_examples.py` executes opted-in fences as subprocesses,
`test_generated_reference.py` regenerates five reference pages from the code, and
`check_docs_impact.py` reports which pages a branch's source changes affect. The
`docs_sources:` front-matter block on each page naming which source files it
depends on is a genuinely good idea.

`build-your-deck/workflows.mdx` gets the `TurnResult`-versus-value distinction
exactly right, in a table, which is more than the README manages.

### The gaps

| issue | detail |
|---|---|
| README's headline example does not run | AD-01; README is parsed but never executed |
| `pause-resume.mdx` is 17 lines and omits turn replay | AD-08, the highest-consequence omission in the docs |
| `CLAUDE.md` §2 names `agentdeck/surfaces/` | does not exist; this is the agent-facing spec |
| five removed symbols cited in live docstrings | AD-25 |
| `docs/delivery/` is 27 files of historical plans | unmarked as historical, cited by live code |
| quickstart teaches the three-concept path | philosophy names `await deck.run(...)` as the one obvious path (AD-24) |
| `docs/engineering/architecture.md` says 10 contracts | there are 12 |

### Documented behavior not enforced

- *"A port with one implementation has not earned itself"*: `ToolSourcePort`
  (AD-15).
- *"Comments: Extremely rare"*: 32% prose (AD-23).
- *"Zero unnecessary abstractions. Delete dead code aggressively"*:
  `runtime/capture.py`, `authoring/runners/`, `resolve_agent_mcp_servers`.
- *"No unprincipled `Any`"*: the entire authoring surface (AD-17).
- `Deck.aclose`'s own docstring on parked workflows (AD-04).
- `Runtime.signal`'s docstring on cancel cascade (AD-12).

The pattern is worth stating plainly: the standards are unusually well written and
the enforcement is unusually thorough for the things a tool can check (imports,
types, lint, snapshots, fence execution). The rules that are *only* prose are the
ones that have quietly stopped holding.

---

## 10. Developer-experience assessment

### A new developer

Install is one line, seven base dependencies. `Deck(agents=[Agent(...)])` then
`async with deck: await deck.run(...)` works and is genuinely short.

Then three things happen. Opening the deck prints three WARNING lines telling them
their default configuration is wrong (`AGENTDECK_EVENTS`, `AGENTDECK_CONTROL`
twice). Copying the README's workflow example raises `ConfigError` (AD-01). Fixing
that by name raises a pydantic `ValidationError` about `DataBlock` (AD-07).

The quickstart's own troubleshooting section is excellent and clearly written by
someone who watched real people fail. That instinct has not reached the README.

### An experienced agent-framework developer

This is the audience the product is strongest for. They will recognise
immediately that the event log is the source of truth, that claims are conditional
appends, and that the lifecycle is one table. Those are the three things that
distinguish a serious runtime from a wrapper, and all three are here and correct.

They will also immediately ask two questions the docs do not answer: what happens
to a parked workflow when the process restarts (AD-04, and the honest answer is
"it is lost, and the session with it"), and what resume costs for an agent turn
(AD-08). Both answers exist, in internal docstrings.

### Someone integrating an existing agent or tool

Good. A plain `agents.Agent` can be a handoff target. `@function_tool` objects
pass through. `RunContext` refuses to be a pydantic field with a five-line error
naming the exact fix, which is the single best error message in the codebase. MCP
is a path and a name.

The friction is `ctx.invoke`'s object-versus-name asymmetry (AD-02) and the
`openai==2.32.0` exact pin (AD-26), which will block a meaningful fraction of
integrations at install time.

### Someone debugging production agent behavior

This is where AgentDeck is furthest ahead of the alternatives. `run.events()`
replays the canonical log from any process. `seq` is dense, so a gap is real.
`origin` names the invocable a caller addressed rather than whatever the engine
handed off to. `usage` rolls up the delegation tree. `views` narrows a tap
declaratively. Langfuse traces are rendered from the log rather than from engine
hooks, so an agent turn and a workflow trace identically.

Two blind spots. A run that a Deck close abandoned leaves no explanation in the
log (AD-04). And a cancelled run's children on another worker keep going with
nothing recording that they were meant to stop (AD-12).

### The mental-model tax, net

| AgentDeck makes simpler | AgentDeck charges for |
|---|---|
| run identity, and never deriving it from user input | `TurnResult` vs value, per invocable kind |
| durable event log with no counter to get wrong | segment boundaries leaking into `events(follow=)` |
| one-turn-per-session across processes | one Deck per process, for reasons that are fixable |
| pause / resume / cancel as one table | that resume for an agent means replay |
| human-in-the-loop with option validation | that a parked workflow does not survive the process |
| observers that cannot slow a run | that two shipped observers can |

The left column is worth more than the right column costs. That is the
product's case, and it holds.

---

## 11. Technical debt themes

**T1. Global state pinned the product to one Deck per process.** `MCPLifecycle`
class attributes and `get_settings`'s cache are the cause; `_live_deck` and the
documented restriction are the symptom. Fixing the cause deletes the symptom, the
`Deck._release` test seam, and AD-05. This is the highest-leverage structural
change available.

**T2. The API grew by addition, never by extension.** Five ways to start a run,
four to read one, four exception families, two ways to run an agent. Each was a
reasonable local decision. The aggregate is a surface a user cannot predict, which
is precisely the failure mode the philosophy names.

**T3. Rules live in prose, and prose goes stale.** 32% of source is docstring and
comment, several rulings exist nowhere else, and five removed symbols are still
cited by live code. The tooling investment (slopcheck, concept budget, docs
impact) shows the team knows; the mechanism chosen (write the reasoning inline)
has the failure mode this audit measured.

**T4. Teardown is the least-tested seam.** Every finding in the High band except
AD-02 and AD-08 is a shutdown or a second-lifecycle issue. `__aenter__` rollback
is careful, `aclose` is not, and nothing tests the combination of a Deck closing
while something is unfinished.

**T5. Speculative surface persists past its evidence.** `ToolSourcePort` with no
callers, `suspendable` with one value, three event kinds with no producer,
`runtime/capture.py`, `authoring/runners/`. The project's own standards forbid all
of it. This is small in volume and matters mainly as a signal about T3: the rules
are not being enforced on the code that predates them.

---

## 12. Recommended prioritization

### Fix soon

| ID | why now |
|---|---|
| AD-01 | the first code a user copies does not run |
| AD-03 | silent wrong answer on the primary API |
| AD-04 | permanently wedged sessions, a poisoned approval inbox, lost answers |
| AD-02 | root cause of AD-01, one branch of code |
| AD-07 | the most obvious composition in the framework fails with an internal error |
| AD-05 | sequential Decks are documented as supported and are broken |
| AD-06 | `except AgentdeckError` does not catch the most common failure |
| AD-08 | doc-only, and the highest-consequence unstated behavior |
| AD-10 | four resources leak on one raise |
| AD-13 | `assert` on a public path, wrong answer under `-O` |
| AD-18 | it makes the dispatch guarantee real rather than nominal |

### Fix opportunistically

`AD-09` (delete `Agent.run` and `authoring/runners/`), `AD-16` (delete
`capture.py`), `AD-15` (route through the port or delete it), `AD-25` (stale
references, mostly falls out of the above), `AD-20` (CLI resume), `AD-21`
(document `pause` on a waiting run), `AD-24` (make `deck.run` the taught path),
`AD-26` (loosen the pins at the next release), `AD-11` (change the `serve` default
at the next major).

### Architectural decisions

**AD-A1. Should `MCPLifecycle` and `Settings` belong to a `Deck`?** Making both
instance state removes the one-Deck-per-process rule, `_live_deck`,
`Deck._release`, AD-05 and AD-19 together. It is the largest available deletion.
Do not fix AD-05 locally without deciding this first, because the local fix is
`shutdown()` clearing three more class attributes, which entrenches the design.

**AD-A2. What does AgentDeck owe a suspended run whose process is gone?** Today:
the session is held forever, `pending()` lists it, answering it fails and marks it
`FAILED`. The options are (a) seal it at deck close, which fixes the common case
and is what AD-04 recommends; (b) durable replay of imperative workflows, which is
the real answer and is large; (c) a reaper with an explicit TTL for suspended
runs, which trades a wedged session for a destroyed approval and the code already
argues against it. Do (a) now; (b) is a roadmap item.

**AD-A3. Should `deck.run` return different shapes for agents and workflows?**
The `TurnResult`-versus-value split causes AD-07, forces `-> TurnResult | Any`,
and is the concept in section 2.1 that does not pay for itself. Either always
return a `TurnResult` (workflows get `.output`), or never (agents get their text
and `usage` moves to the log, which it already is). Both are breaking. Not worth
doing until a major, and worth deciding before more surface is built on the split.

**AD-A4. Where do rulings live?** T3 is not fixable by deleting comments; the
rules are real and are written nowhere else. The decision is whether
`docs/design/` becomes the normative home with pointers in code, or the code stays
normative and the prose ratio is accepted and capped. Either is defensible; the
current state (both, informally) is what lets AD-25 happen.

**AD-A5. Should `Deck` be split?** 1,722 lines and ten responsibilities. The
counter-argument in the code (circular imports) covers `Run` and `Runs` only. This
is a genuine question and not urgent; it should follow AD-A1, since removing the
process claim and the settings globals takes a meaningful slice out of `Deck`
first.

### Leave alone

| what | why |
|---|---|
| `core/status.py`'s four tables | the best code in the repository; do not "simplify" it |
| persist-fan-out-yield, and store-assigned `seq`/`ts` | the invariant everything else rests on |
| `SinkDispatch`'s breaker and probe (AD-27) | over-served but correct, tested, and the NFR is real |
| `Executor.suspendable` (AD-14) | already marked as a known shortcut; removing a public `run.can` costs more than keeping it |
| `ControlVerb` / `SafePoint` closed at birth | correct for a wire enum an old reader must parse |
| `GatewayError` as a separate root | an SPI boundary earning its own root is defensible; document it, do not merge it |
| `ctx.ask` / `run.answer` semantics | the option refusal before the claim is exactly right |
| the four-store contract suite | expensive, and it is why the store layer can be trusted |
| `agentdeck.testing` | a public test harness that stubs only the SDK boundary is the right design |
