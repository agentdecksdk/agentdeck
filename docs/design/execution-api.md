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

There is no `ctx.approve()` either: an approval is a question with two options, and
`ask(question, options=[True, False])` already says it. One mechanism that takes any option set
beats two that overlap, and it keeps AgentDeck out of deciding what counts as a yes.

## `ctx`

`ToolCtx ⊂ WorkflowCtx`. A tool performs a capability; a workflow coordinates executions, so only
a workflow gets the orchestration half.

| member | in | today |
|---|---|---|
| `data` | ToolCtx | `Context.data`, unchanged |
| `reporter` | ToolCtx | `Context.reporter`, new methods (below) |
| `agent` | ToolCtx | new: the current `AgentInstance`, or `None`. Lands with PR7 |
| `safepoint()` | ToolCtx | `Context.checkpoint()`, renamed |
| `invoke(target, input)` | WorkflowCtx | new |
| `parallel(*runs)` | WorkflowCtx | new |
| `ask(question, options=...)` | WorkflowCtx | new public form of today's langgraph interrupt |
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

It already answers that question, and `InvocableSpec{name, kind, engine, native}` is already the
neutral description of a target. Three things change, and the adapters move with the name:
`InvocableSpec.engine` becomes `.executor`, `adapters/engines/` becomes `adapters/executors/`, and
`LangGraphEngine` becomes `LangGraphExecutor`.

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

A third piece is new in `core.invocable`: `NativeInvocable`, the protocol an AgentDeck-native
definition satisfies, so a pure adapter can play a `@tool`/`@workflow` without importing
`authoring`. It lands with the native executor.

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

`ctx.ask()` parks by the same mechanism. This is what makes a native workflow suspendable in the
`run.can` table, and it is also its ceiling: a parked body lives in one process, and surviving a
restart is the deferred replay model.

## An answer is refused before it is claimed

Only a question that named its `options` can be judged from outside the body; a free-form one
takes whatever it is given, because nothing outside can judge it better than the body can.

| | |
|---|---|
| where it is checked | `Runtime.resume`, before the claim. Every surface (in-process, HTTP, CLI) inherits it, because all of them arrive there |
| what the answerer gets | the error at their own call, naming the options |
| what the run does | nothing: it is still `WAITING_ANSWER`, and the next answer lands |
| what the log gets | one `answer.refused`, so an audit sees the attempt. Not a lifecycle kind, and it names the type that arrived rather than the value, which can be anything the answerer typed |

Checked before the claim rather than inside the body, because the claim *is* the `run.resumed`
carrying the answer: a value refused after it lands leaves the run resumed, the body raising, and
nobody able to answer it properly.

## Where LangGraph ends up

Out of v5.0.0 entirely. A prebuilt graph is a target, not a declaration to wrap, and the one
invocation boundary that would run it is #337, which is the next release.

| today | v5.0.0 | when the resolver lands |
|---|---|---|
| `Workflow(graph=build_graph)` in `Deck(workflows=[...])` | removed | the compiled graph itself, invoked or registered directly |
| a `WorkflowDeclaration` subclass | removed | `@workflow` already replaced it |
| `durable=True`, and AgentDeck wires langgraph's checkpointer | removed | the author compiles their graph with the checkpointer they want |

Removal leads the replacement rather than following it, which is the reverse of what an earlier
revision of this file said. The reason is that the alternative is a deprecation window, and a
deprecated API is still an API: documented, supported, tested, and teaching the shape this design
just rejected. `Workflow`, `WorkflowDeclaration`, `graph=` and `durable=` go, together with what
hangs off them (`sleep_until` and the timer sweep, `Workflow.pending`, `as_tool`, the serve
surface's workflow routes), the adapter itself, and the three LangGraph dependencies.

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
| `EnginePort.start()/resume()` | `Executor.execute()` | `resume` was never the pause's resume, and the answer it took is already in the log |
| `run.answer(value)` logs a warning | raises `ValueError` | a value the log cannot carry resumed a run no replay could reproduce |
| `Workflow(graph=...)`, `WorkflowDeclaration`, `durable=` | removed; write an imperative `@workflow` | #336 |
| LangGraph as a supported runtime | removed from v5.0.0, adapter and dependencies included | #337 restores it as a target |

## Rejected from the source design

The design this file was written from is adopted whole except:

| proposed | here | why |
|---|---|---|
| `Suspendable` / `Cancelable` protocols | `Executor.suspendable` ClassVar | see Executor above |
| silent on durable replay | deferred, stated below | #336 requires it and the draft does not say how |
| pause raises at every safepoint | a native workflow parks | a raise through an imperative body destroys the state that is the workflow |

#336 sketches `ctx.run.ask()` / `ctx.run.pause()` / `ctx.run.wait()`. This file rules `ctx.ask()`
alone, with no `ctx.approve()` and no `ctx.pause()`, and both issues are updated to match.

## Out of scope for v5.0.0

| deferred | why, and what unblocks it |
|---|---|
| durable replay of an imperative `@workflow` | v5.0.0 suspends and resumes in one process. A body that survives a restart without re-executing committed invocations needs an invocation journal; its own issue and design |
| `InvocationResolver` and the wrapping adapters | #337. Nothing foreign is adapted until the thing it is adapted *to* exists and is proven, and it now has a whole release to be proven in |
| LangGraph, entirely | see below |
| streaming / checkpointing capability protocols | nothing needs AgentDeck to control them yet |

### LangGraph is removed, not deprecated

**Decided 2026-08-21.** v5.0.0 ships native targets and the OpenAI Agents SDK. `Workflow`,
`WorkflowDeclaration`, `graph=`, `durable=`, `adapters/executors/langgraph/`, the checkpointer
resolution behind `AGENTDECK_CHECKPOINT`, and the `langgraph` / `langchain-core` /
`langgraph-checkpoint-sqlite` dependencies all go.

The reasoning is that half-keeping it is worse than either alternative.

| | |
|---|---|
| the declaration had to die | `Workflow(graph=StateGraph)` names one framework in the neutral authoring layer, which is the thing this whole design removes |
| the resolver that would replace it is not ready | #337 is the largest remaining piece, and #330 means a wrapped graph's durable identity crosses namespaces, so it would land non-durable |
| keeping the adapter dormant costs every user | three LangGraph packages are required dependencies today, for a path 5.0 would give nobody a way to reach |

What a 4.x graph user does: stay on 4.x, or port the graph to an imperative `@workflow`, which is
ordinary Python and needs no state schema. LangGraph returns when the resolver does, as a target
the resolver runs rather than a declaration the authoring layer knows about.

This changes the product's own description: `README.md` and `CLAUDE.md` both open by naming
LangGraph, and the removal PR owns correcting them.

## Delivery

Native first, wrapping later: nothing foreign is adapted until the thing it is being adapted *to*
exists and is proven by AgentDeck's own targets. v5.0.0 ends at "proven", and the wrapping is its
own release.

No deprecation window anywhere in this list. A name that is wrong is removed in the release that
decides it is wrong, because a deprecated API is still an API: it is documented, supported,
tested, and it teaches the shape the design just rejected.

| PR | scope | closes |
|---|---|---|
| 1 | this file | - |
| 2 | `run.can`, strict lifecycle ops, `ToolCtx`, `safepoint()`, `Reporter` | - |
| 3 | `EnginePort` becomes `Executor`, `start` + `resume` become `execute` | - |
| 4 | native `@tool` / `@workflow`, `WorkflowCtx`, the native executor, `ask(options=...)`, parking suspension | - |
| 5 | `ctx.invoke` / `ctx.parallel`, child runs, the invoker seam | #336 |
| 6 | `Observer`, `views`, and the `view=` filter on a registration | #211 |
| 7 | `AgentInstance`, `ctx.agent`, `ctx.agents.create()` / `fork()` | #236 |
| 8 | remove LangGraph: `Workflow`, `WorkflowDeclaration`, `graph=`, `durable=`, the adapter, the checkpointer, the dependencies, and the docs that name them | - |

Observability lands at 6 rather than last because every layer above it produces events a view has
to be able to select, and a selector retrofitted over a finished vocabulary is one written from
the outside.

The executor contract is its own PR rather than the native one's first commit: the rename touches
every adapter and the collapse changes what an answer *is*, and a reviewer should not have to read
both against a new executor at the same time. It still lands before anything is wrapped, which was
the ruling. The native path splits for the same reason: a new executor and a new context type is
one read, and a seam that reaches from an executor back out to the Deck is another.

Two lines PR5 does not cross:

| | |
|---|---|
| what `ctx.invoke()` accepts | a catalog name and a native definition, nothing else. Every bare object, a plain callable included, waits for PR8's resolver, so there is one rule and no special case |
| the parent edge | no parent field on `run.started`, where the parent holds the child handle in memory. `RunStarted` dropped `parent_run_id` once already for being written and never read; it comes back in PR8 with the invocation tree that reads it |

`ctx.parallel` fails all-or-nothing: the first failure cancels its siblings and propagates, the way
`asyncio.TaskGroup` does. A workflow body is ordinary Python, so an exception is an exception, and
no child is left running behind a parent that already gave up. Gather-with-exceptions is the shape
that gets misused, because a body that forgets to inspect the list gets a silent wrong answer.

## Amended by PR7 (2026-08-22, #236)

| ruled here | shipped | why |
|---|---|---|
| the parent edge lands in PR8 | PR7 | the readers land in PR7: a cancel cascade, a usage roll-up and the depth bound all follow the edge, and two of the three have to work on a log nobody watched live |
| `agents.fork()` copies `ctx.agent` | `fork(source, **overrides)`, source required | `ctx.agents` is `WorkflowCtx`'s and a workflow is not an agent, so `ctx.agent` is always `None` where `fork` is reachable. A default that can only be `None` is a dead API |

## Open

| | |
|---|---|
| `run.started` for a foreign target | `kind_of_invocable` is a closed literal and a resolved foreign object matches none of its members. PR8 either widens it or records the executor instead |
