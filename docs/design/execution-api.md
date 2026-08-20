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
| `agent` | ToolCtx | new: the current `AgentInstance`, or `None`. Lands with PR4 |
| `safepoint()` | ToolCtx | `Context.checkpoint()`, renamed |
| `invoke(target, input)` | WorkflowCtx | new |
| `parallel(*runs)` | WorkflowCtx | new |
| `ask(prompt)` / `approve(prompt)` | WorkflowCtx | new public form of today's langgraph interrupt |
| `agents.create()` / `agents.fork()` | WorkflowCtx | new, and last: see Delivery |

`ctx` does not grow into a runtime namespace. Nothing else goes on it without a ruling.

`ToolCtx[T]` replaces `Context[T]` as the declared parameter type, and `WorkflowCtx[T]` is what an
imperative `@workflow` body declares. Declaring the wrong one is a `build()` error, not a runtime
surprise: a tool that asks for `WorkflowCtx` would silently acquire orchestration it must not have.

### Input binds like a call

`ctx.invoke(target, *args, **kwargs)` binds to the target's own signature, exactly as calling it
would:

```python
await ctx.invoke(load_customer, ticket.customer_id)
await ctx.invoke(search_web, query=topic)
```

A run started from outside has one value rather than an argument list, so `deck.run(name, value)`
and `deck.runs.start(name, value)` bind a `dict` as keywords and anything else as the single first
parameter. That is what keeps JSON over HTTP working and a one-argument workflow short:

| call | body |
|---|---|
| `deck.run("research", "agentdeck")` | `research(ctx, topic)` gets `topic="agentdeck"` |
| `deck.run("resolve", {"ticket": t, "urgent": True})` | `resolve(ctx, ticket, urgent)` gets both |

A body whose single parameter is itself a mapping takes it whole, because binding is by signature:
one parameter, one value.

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

`EnginePort` becomes `Executor`. There is no second execution contract: the port that already
answers "how is this target executed" takes the name, rather than a new type arriving beside it.

It already answers that question, and `InvocableSpec{name, kind, executor, native}` is already the
neutral description of a target. Two things change, and the adapters move with the name:
`adapters/engines/` becomes `adapters/executors/`, and `LangGraphEngine` becomes
`LangGraphExecutor`.

**One method, not two.** `start` and `resume` are collapsed into a single `execute`:

```python
class Executor(ABC):
    name: ClassVar[str]
    suspendable: ClassVar[bool]

    def execute(self, spec, input, history, ctx) -> AsyncGenerator[KnownPayload, None]: ...
```

`input` is always what the run was opened with. `history` is the log so far, and it is what says
which play this is.

`resume` was never the pause's resume. Lifting a pause already re-enters `start` with the log as
history, because a paused turn left no stack to return to; `resume` existed only for the answer to
an interrupt, where LangGraph needs `Command(resume=value)` rather than a state. The log already
carries that: `RunResumed.value` stores the answer in full, and the history tail says which of the
three plays this is.

| history ends on | the play is |
|---|---|
| the previous run's terminal event, or nothing | fresh |
| `run.paused`, `run.resumed` | a lifted pause, replayed |
| `run.interrupted`, `run.resumed` | an answered interrupt |

The `thread_id` parameter goes with it: it is a LangGraph concept on a neutral port, and history's
last `run.interrupted` carries the thread the executor itself wrote there. What this buys is the
wrapping adapters: a plain callable and an Agents SDK object have one way in, and neither has to
implement a second method it cannot mean.

One consequence, and it is a break: the answer is whatever the log says it is. Today a value JSON
cannot carry is dropped from the log with a warning and still handed to the engine in memory, so a
run resumes on an answer no replay could ever reproduce. `run.answer(...)` refuses such a value
instead.

**The front half is missing.**

```text
target object -> InvocationResolver -> InvocableSpec -> Executor -> Run
```

| piece | job |
|---|---|
| `InvocationResolver` | what is this object, and which executor runs it |
| `Executor` | play it, yield payloads |
| `Runtime` | identity, ordering, persistence, control |

Capability is a declaration, not a pair of methods:

```python
class Executor(ABC):
    name: ClassVar[str]
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

## Where LangGraph ends up

`Workflow(graph=...)` goes away. A prebuilt graph is a target, not a declaration to wrap, and the
one invocation boundary is what runs it.

| today | v5 |
|---|---|
| `Workflow(graph=build_graph)` in `Deck(workflows=[...])` | the compiled graph itself, invoked or registered directly |
| a `WorkflowDeclaration` subclass | `@workflow`, an ordinary Python body |
| `durable=True`, and AgentDeck wires langgraph's checkpointer | the author compiles their graph with the checkpointer they want, and AgentDeck runs it |

Removal follows the replacement rather than leading it: the resolver is what makes a bare graph
invocable, so `Workflow`, `WorkflowDeclaration`, `graph=` and `durable=` are deleted in the PR
after it, together with what hangs off them (`sleep_until` and the timer sweep, `Workflow.pending`,
`as_tool`, the serve surface's workflow routes).

Two consequences, stated rather than discovered later:

| | |
|---|---|
| AgentDeck stops owning workflow durability | a durable graph is durable because its author compiled a checkpointer into it. AgentDeck's own answer is the deferred replay model, and until that lands a native `@workflow` does not survive a restart |
| #330 stops being AgentDeck's bug to fix | a thread id with no namespace is a property of the graph the author compiled and checkpointed. What AgentDeck owes is to say so where a graph is registered, not to key somebody else's checkpointer |

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
| `Workflow(graph=...)` | `@workflow` body; a graph is a target the resolver runs | #336, #337 |
| `deck.runs.start(name, ...)` | also `start(target, ...)` | #337 |

## Rejected from the source design

The design this file was written from is adopted whole except:

| proposed | here | why |
|---|---|---|
| `Suspendable` / `Cancelable` protocols | `Executor.suspendable` ClassVar | see Executor above |
| silent on durable replay | deferred, stated below | #336 requires it and the draft does not say how |
| pause raises at every safepoint | a native workflow parks | a raise through an imperative body destroys the state that is the workflow |

#336 sketches `ctx.run.ask()` / `ctx.run.pause()` / `ctx.run.wait()`. This file rules `ctx.ask()`
and `ctx.approve()` instead, with no `ctx.pause()` at all, and both issues are updated to match.

## Out of scope for v5.0.0

| deferred | why, and what unblocks it |
|---|---|
| durable replay of an imperative `@workflow` | v5.0.0 suspends and resumes in one process. A body that survives a restart without re-executing committed invocations needs an invocation journal; its own issue and design |
| durability for a wrapped LangGraph graph | #330: the checkpointer keys a thread by id alone, so a wrapped graph's durable identity crosses namespaces. Wrapping lands non-durable |
| streaming / checkpointing capability protocols | nothing needs AgentDeck to control them yet |

## Delivery

Native first, wrapping last: nothing foreign is adapted until the thing it is being adapted *to*
exists and is proven by AgentDeck's own targets.

| PR | scope | closes |
|---|---|---|
| 1 | this file | - |
| 2 | `run.can`, strict lifecycle ops, `ToolCtx`, `safepoint()`, `Reporter` | - |
| 3 | `EnginePort` becomes `Executor`, `start` + `resume` become `execute` | - |
| 4 | native `@tool` / `@workflow`, `WorkflowCtx`, the native executor, `ask` / `approve`, parking suspension | - |
| 5 | `ctx.invoke` / `ctx.parallel`, child runs, the invoker seam | #336 |
| 6 | `AgentInstance`, `ctx.agent`, `ctx.agents.create()` / `fork()` | #236 |
| 7 | `InvocationResolver` and the wrapping adapters: a LangGraph graph, an Agents SDK object, a plain callable, `deck.runs.start(target)` | #337 |
| 8 | migration: delete `Workflow`, `WorkflowDeclaration`, `graph=`, `durable=` and what hangs off them | - |

The executor contract is its own PR rather than the native one's first commit: the rename touches
every adapter and the collapse changes what an answer *is*, and a reviewer should not have to read
both against a new executor at the same time. It still lands before anything is wrapped, which was
the ruling. The native path splits for the same reason: a new executor and a new context type is
one read, and a seam that reaches from an executor back out to the Deck is another.

Two lines PR5 does not cross:

| | |
|---|---|
| what `ctx.invoke()` accepts | a catalog name and a native definition, nothing else. Every bare object, a plain callable included, waits for PR7's resolver, so there is one rule and no special case |
| the parent edge | no parent field on `run.started`, where the parent holds the child handle in memory. `RunStarted` dropped `parent_run_id` once already for being written and never read; it comes back in PR7 with the invocation tree that reads it |

## Open

| | |
|---|---|
| what the parent edge is called | the field PR3 puts on `run.started` for cancel cascade, usage roll-up and trace nesting. `parent_run_id` is the obvious name and the one that was already removed once |
| `run.started` for a foreign target | `kind_of_invocable` is a closed literal and a resolved foreign object matches none of its members. PR7 either widens it or records the executor instead |
| `ctx.parallel` failure policy | all-or-nothing, or gather-with-exceptions. Undecided |
