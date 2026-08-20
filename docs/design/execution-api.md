# The execution API

What `ctx`, `Run`, `Reporter` and `deck.runs` are, and what teaches AgentDeck to execute an
arbitrary target. Rules #336 (imperative `@workflow`) and #337 (one universal invocable boundary);
constrains #211 (reporter), and #236 / #249 / #304 inherit its vocabulary.

Decided 2026-08-20. Ships across v5.0.0. No v4 compatibility: the names below replace what 4.x
exposed rather than sitting beside them.

## The ruling

Anything executable becomes an AgentDeck Run: a workflow, a tool, an agent, an OpenAI Agents SDK
agent, a LangGraph graph, a plain callable.

> Execution is mandatory. Control is capability-based.

A target does not implement the AgentDeck lifecycle to be run. It gets a Run identity, an event
stream and a result regardless; pause, resume and cancel are available to the extent its executor
can honour them, and `run.can.*` is where a caller reads that.

## Four namespaces

| namespace | question it answers |
|---|---|
| `ctx.*` | what code can do from inside an execution |
| `Run` | one execution: identity, status, control, events, result |
| `Reporter` | what running code intentionally publishes outward |
| `deck.runs.*` | how an application or operator acts on runs from outside |

`ctx.ask()` suspends *my own* branch. `child.pause()` controls *another* execution. A generic
`ctx.pause()` cannot say which, so there is none.

## `ctx`

`ToolCtx ⊂ WorkflowCtx`. A tool performs a capability; a workflow coordinates executions, so only
a workflow gets the orchestration half.

| member | in | today |
|---|---|---|
| `data` | ToolCtx | `Context.data`, unchanged |
| `reporter` | ToolCtx | `Context.reporter`, new methods (below) |
| `agent` | ToolCtx | new: the current `AgentInstance`, or `None` |
| `safepoint()` | ToolCtx | `Context.checkpoint()`, renamed |
| `invoke(target, input)` | WorkflowCtx | new |
| `parallel(*runs)` | WorkflowCtx | new |
| `ask(prompt)` / `approve(prompt)` | WorkflowCtx | new public form of today's langgraph interrupt |
| `agents.create()` / `agents.fork()` | WorkflowCtx | deferred, see Out of scope |

`ctx` does not grow into a runtime namespace. Nothing else goes on it without a ruling.

`ToolCtx[T]` replaces `Context[T]` as the declared parameter type, and `WorkflowCtx[T]` is what an
imperative `@workflow` body declares. Declaring the wrong one is a `build()` error, not a runtime
surprise: a tool that asks for `WorkflowCtx` would silently acquire orchestration it must not have.

## `Run`

`ctx.invoke()` returns a child Run; `deck.runs.start()` returns a top-level one. Same object, same
control surface, whichever side it came from.

```python
result = await ctx.invoke(agent, input)          # the short path: Run is awaitable

child = ctx.invoke(agent, input)                 # the same call, held
if child.can.pause:
    await child.pause()
result = await child
```

| member | note |
|---|---|
| `id`, `status` | as 4.x |
| `can.pause` / `can.resume` / `can.cancel` | new |
| `pause()` / `resume()` / `cancel()` | strict: they raise instead of returning `False` |
| `events()` | as 4.x |
| `__await__` | as 4.x |

## `run.can.*`

`can` answers "is this control available on this Run right now", not "does the executor support
it". Three inputs, one answer:

```text
executor declares the control  +  PRECONDITIONS[status, operation] is LEGAL  =  run.can.<op>
```

| executor | status | `can.pause` | `can.resume` | `can.cancel` |
|---|---|---|---|---|
| suspendable | RUNNING | True | False | True |
| suspendable | PAUSED | False | True | True |
| suspendable | COMPLETED | False | False | False |
| plain callable | RUNNING | False | False | True |

The legality half is `core/status.py`'s existing table, extended with `Operation.PAUSE` and
`Operation.CANCEL` rows, and the whole derivation is one pure function beside it. No second
lifecycle table anywhere: `run-lifecycle.md` still holds.

`can` reads the status the handle last saw, so it stays a plain attribute: `if run.can.pause`, not
an `await`. Every way a handle is made already knows a status (`start()` just opened the run,
`get()` and `list()` read one), and every op that talks to the store refreshes it.

That is not the cached state `run-identity.md` §3 forbids. `status()` and the lifecycle methods
never read the snapshot, so nothing authoritative is answered from it: `can` is informational by
ruling, and a snapshot is exactly what "informational" means. The run may finish between the read
and the call, which is why the strict method stays authoritative and a caller that races catches
`RunStateError`. This is also why there is no `try_pause()`: a boolean return that means four
different things is what 4.x had.

## Executor

The executor is `EnginePort`, unchanged in shape. There is no second execution contract.

`EnginePort.start(spec, input, history, ctx)` already answers "how is this target executed", and
`InvocableSpec{name, kind, engine, native}` is already the engine-neutral description of a target.
What is missing is only the front half:

```text
target object -> InvocationResolver -> InvocableSpec -> EnginePort -> Run
```

| piece | job |
|---|---|
| `InvocationResolver` | what is this object, and which engine runs it |
| `EnginePort` | play it, yield payloads |
| `Runtime` | identity, ordering, persistence, control |

Capability is a declaration, not a pair of methods:

```python
class EnginePort(ABC):
    engine: ClassVar[str]
    suspendable: ClassVar[bool]
```

The source design proposed `Suspendable` / `Cancelable` protocols with `pause()` and `resume()`
methods. Rejected: pause, resume and cancel are already implemented once, by the Runtime's control
port and the cooperative `Gate`, and no engine implements them today. A protocol whose methods
every engine would satisfy by delegating back to the Runtime declares a capability in the most
expensive way available. A `ClassVar` says the same thing beside `engine`, which is the idiom
already there.

One flag, not two. Pause and resume are one reversible capability (the source design's own
argument for `Suspendable` over `Pausable` + `Resumable`), and a boolean cannot express the
half-capability that argument rejects. There is no `cancelable` flag because no executor has
needed one: a run can always be ended, and the flag arrives with the first target that cannot be.

## Suspension parks the body, it does not raise through it

`Gate.checkpoint()` raises today, and that is right for the two engines that have it: a graph node
or an agent turn that unwinds is re-entered from its own checkpoint on resume.

An imperative `@workflow` has no checkpoint. Raising through the body destroys the local state that
*is* the workflow, and with durable replay deferred there is nothing to rebuild it from. So in a
native workflow:

| control | at a safepoint |
|---|---|
| pause | the body parks in place and awaits the resume, coroutine and locals alive |
| cancel | raises, because the run ends and there is nothing left to preserve |

`ctx.ask()` and `ctx.approve()` park by the same mechanism. This is what makes a native workflow
suspendable in the `run.can` table, and it is also its ceiling: a parked body lives in one
process, and surviving a restart is the deferred replay model.

## Reporter

Four methods, one event kind.

| method | meaning |
|---|---|
| `info(msg, **fields)` / `warning(...)` / `error(...)` | levelled prose from running code |
| `report(name, **fields)` | a structured application record |

Replaces `status()` / `progress()`, and their two payloads collapse into one `report` event
carrying level, name and fields. Origin is never passed by the caller: the `ctx` that carries the
reporter is what associates the report with its run and branch.

Reports are not events. Events are what the runtime did (`run.started`, `tool.started`,
`child.started`); reports are what the software chose to say. They share transport and stay
distinct in the API. #211 is where the transport and sink half is settled.

## Divergence from 4.x

Each row is a break, and each needs a CHANGELOG entry.

| 4.x | v5.0.0 | why |
|---|---|---|
| `Run.pause()/cancel() -> bool` | raise on refusal | `False` meant "no control backend" and was indistinguishable from "already over" |
| `Context[T]` | `ToolCtx[T]` / `WorkflowCtx[T]` | one context type cannot express that a tool may not orchestrate |
| `Context.checkpoint()` | `ctx.safepoint()` | same `Gate`, the name a caller can guess |
| `Reporter.status()/progress()` | `info/warning/error/report` | two hard-coded shapes, no structured record |
| `Workflow(graph=...)` only | `@workflow` body, graph still accepted | #336 |
| `deck.runs.start(name, ...)` | also `start(target, ...)` | #337 |

## Rejected from the source design

The design this file was written from is adopted whole except:

| proposed | here | why |
|---|---|---|
| `Executor` protocol with `execute()` | `EnginePort`, unchanged | a parallel contract for what already exists |
| `Suspendable` / `Cancelable` protocols | `EnginePort.suspendable` ClassVar | see Executor above |
| `ctx.agents.create/fork` in the baseline | deferred | agent instances are #236's own lifecycle concept |
| silent on durable replay | deferred, stated below | #336 requires it and the draft does not say how |
| pause raises at every safepoint | a native workflow parks | a raise through an imperative body destroys the state that is the workflow |

#336 sketches `ctx.run.ask()` / `ctx.run.pause()` / `ctx.run.wait()`. This file rules `ctx.ask()`
and `ctx.approve()` instead, with no `ctx.pause()` at all, and both issues are updated to match.

## Out of scope for v5.0.0

| deferred | why, and what unblocks it |
|---|---|
| durable replay of an imperative `@workflow` | v5.0.0 suspends and resumes in one process. A body that survives a restart without re-executing committed invocations needs an invocation journal; its own issue and design |
| `ctx.agents.create()` / `fork()` | agent instances are a new lifecycle concept, and #236 already owns it |
| durability for a wrapped LangGraph graph | #330: the checkpointer keys a thread by id alone, so a wrapped graph's durable identity crosses namespaces. Wrapping lands non-durable |
| streaming / checkpointing capability protocols | nothing needs AgentDeck to control them yet |

## Delivery

| PR | scope | closes |
|---|---|---|
| 1 | this file | - |
| 2 | `run.can`, strict lifecycle ops, `ToolCtx`/`WorkflowCtx`, `@tool`/`@workflow` validation, `ctx.invoke`/`parallel`/`safepoint`, child runs, reporter | #336 |
| 3 | `InvocationResolver`, foreign executors (Agents SDK object, LangGraph graph, plain callable), `deck.runs.start(target)` | #337 |

Two lines PR2 does not cross:

| | |
|---|---|
| what `ctx.invoke()` accepts in PR2 | a catalog name and a native definition, nothing else. Every bare object, a plain callable included, waits for PR3's resolver, so there is one rule and no special case |
| the parent edge | no parent field on `run.started` in PR2, where the parent holds the child handle in memory. `RunStarted` dropped `parent_run_id` once already for being written and never read; it comes back in PR3 with the invocation tree that reads it |

## Open

| | |
|---|---|
| what the parent edge is called | the field PR3 puts on `run.started` for cancel cascade, usage roll-up and trace nesting. `parent_run_id` is the obvious name and the one that was already removed once |
| `run.started` for a foreign target | `kind_of_invocable` is a closed literal and a resolved foreign object matches none of its members. PR3 either widens it or records the engine instead |
| `ctx.parallel` failure policy | all-or-nothing, or gather-with-exceptions. Undecided |
