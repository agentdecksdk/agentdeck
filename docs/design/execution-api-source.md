# AgentDeck Execution API — Final Architecture

**Status:** Canonical API direction
**Date:** 2026-08-20
**Scope:** Execution context, invocation, child runs, lifecycle control, reporting, agent instances, and executor capabilities.

---

## 1. Core Design Ruling

AgentDeck should treat **anything executable as something that can become an AgentDeck Run**.

The execution target may be:

- an AgentDeck workflow,
- an AgentDeck tool,
- an agent instance,
- an OpenAI Agents SDK agent,
- a LangGraph graph,
- another third-party runtime,
- or a plain Python callable.

AgentDeck does **not** require every target to implement the full AgentDeck lifecycle contract.

Instead:

> **Execution is mandatory. Control is capability-based.**

The public API is intentionally split into distinct responsibilities:

- `ctx.*` — what code can do **from inside** an execution.
- `Run` — the execution itself and its lifecycle/control handle.
- `Reporter` — structured information emitted **from** an execution.
- `deck.runs.*` — how an application or operator controls executions **from outside**.
- `Executor` — the minimal internal contract that teaches AgentDeck how a target executes.
- Optional executor capabilities — richer control when the underlying runtime supports it.

This is the cleanest way to preserve AgentDeck's short-path API while supporting arbitrary runtimes without reducing everything to the lowest common denominator.

---

# 2. Mental Model

```text
                     Application / Operator
                              │
                       deck.runs.*
                              │
                              ▼
                    ┌─────────────────┐
                    │       Run       │
                    │ lifecycle/state │
                    │ identity/events │
                    │ control/result  │
                    └────────┬────────┘
                             ▲
                             │
                           ctx.*
                             │
                       running code
                             │
                             ▼
                        ctx.invoke()
                             │
                             ▼
                       child Run(s)
                             │
                             ▼
                         Executor
                             │
                             ▼
                  underlying implementation
```

The important separation is:

```text
ctx.*          = inside-execution programming API
Run            = execution abstraction
run.can.*      = currently available control
run.*()        = strict lifecycle operations
Reporter       = execution → outside communication
deck.runs.*    = outside-execution operations
Executor       = target-specific execution mechanism
```

---

# 3. Baseline `ctx` API

The execution context should stay deliberately small.

```python
ctx.data
ctx.reporter
ctx.agent

ctx.invoke(...)
ctx.parallel(...)

ctx.ask(...)
ctx.approve(...)

ctx.safepoint()

ctx.agents.create(...)
ctx.agents.fork(...)
```

That is the baseline public API.

AgentDeck should resist expanding `ctx` into a generic runtime namespace.

---

## 3.1 `ctx.data`

```python
ctx.data
```

Application-defined context associated with the current execution.

Examples:

```python
tenant_id = ctx.data.tenant_id
db = ctx.data.database
request = ctx.data.request
```

The exact structure belongs to the application.

AgentDeck should propagate it through execution according to its context rules without forcing users into an AgentDeck-specific application data model.

---

## 3.2 `ctx.agent`

```python
ctx.agent
```

Returns the current `AgentInstance` when the execution belongs to an agent, otherwise `None`.

Example:

```python
if ctx.agent is not None:
    ctx.reporter.info(
        "Running inside agent",
        agent_id=ctx.agent.id,
    )
```

This avoids pretending that every workflow or function necessarily has an agent.

---

# 4. `ctx.invoke()` — Universal Execution Primitive

```python
child = ctx.invoke(target, input)
```

The key ruling:

> `ctx.invoke()` creates an AgentDeck-managed child execution and returns its child `Run`.

It should **not** expose adapter resolution, capability negotiation, persistence, event translation, or framework-specific semantics.

Those belong below the public API.

---

## 4.1 Short path

The common case remains extremely simple:

```python
result = await ctx.invoke(agent, input)
```

Because `Run` is awaitable.

This is important: users should not need to understand Runs merely to invoke something.

---

## 4.2 Advanced path

When control or inspection is required:

```python
child = ctx.invoke(agent, input)

print(child.id)
print(child.status)

if child.can.pause:
    await child.pause()

result = await child
```

The same invocation primitive therefore serves both simple and advanced applications without adding flags or control options to `ctx.invoke()` itself.

---

## 4.3 What may be invoked

The target may be any executable for which AgentDeck can resolve an executor:

```python
await ctx.invoke(my_function, input)
await ctx.invoke(my_tool, input)
await ctx.invoke(my_workflow, input)
await ctx.invoke(openai_agent, input)
await ctx.invoke(langgraph_graph, input)
await ctx.invoke(custom_runtime, input)
```

Conceptually:

```text
ctx.invoke(target)
        │
        ▼
Invocation Resolver
        │
        ▼
Executor
        │
        ▼
Child Run
        │
        ▼
Underlying runtime
```

---

# 5. `Run` — The Universal Execution Abstraction

`Run` is the main abstraction AgentDeck owns.

AgentDeck does not need to replace the lifecycle semantics of LangGraph, OpenAI Agents SDK, DeepSeek Harness, or another runtime.

Instead, AgentDeck wraps those executions with its own universal outer lifecycle.

A child returned from `ctx.invoke()` and a top-level Run returned from `deck.runs.start()` should expose the same control surface.

Baseline:

```python
run.id
run.status

run.can.pause
run.can.resume
run.can.cancel

await run.pause()
await run.resume()
await run.cancel()

async for event in run.events():
    ...

result = await run
```

---

## 5.1 Awaitable result

A `Run` should be awaitable:

```python
child = ctx.invoke(agent, input)
result = await child
```

Therefore the concise form remains:

```python
result = await ctx.invoke(agent, input)
```

This is a major API ergonomics win.

The user gets a powerful handle without paying for it in the common case.

---

# 6. `run.can.*`

```python
run.can.pause
run.can.resume
run.can.cancel
```

These answer:

> **Can this control operation currently be applied to this Run?**

They are not simply executor capability flags.

They should combine:

1. underlying executor capability,
2. current Run state,
3. AgentDeck lifecycle rules,
4. AgentDeck policy.

For example:

```text
executor supports suspension = yes
run.status = RUNNING
→ run.can.pause = True

executor supports suspension = yes
run.status = PAUSED
→ run.can.pause = False
→ run.can.resume = True

executor supports suspension = yes
run.status = COMPLETED
→ run.can.pause = False
→ run.can.resume = False
```

---

## 6.1 Why `run.can.pause` instead of `run.can_pause`

The grouped namespace scales better:

```python
run.can.pause
run.can.resume
run.can.cancel
```

It reads as one coherent capability/state view.

It can also grow internally later without changing the outer shape.

---

## 6.2 `can` is informational, not a transaction guarantee

This remains valid:

```python
if run.can.pause:
    await run.pause()
```

But `pause()` may still fail.

The Run could complete between the check and operation.

Therefore:

> `run.can.*` is for discovery, UI state, branching, and ergonomics.
> Strict lifecycle methods remain authoritative.

Example:

```python
if child.can.pause:
    try:
        await child.pause()
    except InvalidRunState:
        # The execution may have completed between the check and call.
        pass
```

This is preferable to building `try_pause()`, silent failure, or increasingly complex policy flags into every lifecycle method.

---

# 7. Strict Lifecycle Operations

```python
await run.pause()
await run.resume()
await run.cancel()
```

These should be strict.

That means:

```python
await run.pause()
```

means:

> Pause this Run according to AgentDeck's lifecycle contract, or raise an explicit control/state error.

AgentDeck should not silently ignore unsupported lifecycle operations.

Possible error categories:

```text
UnsupportedControl
InvalidRunState
ControlFailed
```

Exact naming can be finalized separately.

The important semantic ruling is strictness.

---

# 8. Internal Executor Model

The public Run abstraction should not force every target to implement all lifecycle controls.

The internal minimum is execution.

```python
class Executor(Protocol):
    async def execute(self, ...) -> Any:
        ...
```

This is the critical design rule:

> **If AgentDeck knows how to execute it, it can become a Run.**

Everything else is optional.

---

# 9. Optional Capability Protocols

Pause and resume belong together.

If AgentDeck defines pause as a reversible lifecycle state, supporting pause without resume is not a complete suspension capability.

Therefore use:

```python
class Suspendable(Protocol):
    async def pause(self, ...) -> None:
        ...

    async def resume(self, ...) -> None:
        ...
```

Cancellation remains independent:

```python
class Cancelable(Protocol):
    async def cancel(self, ...) -> None:
        ...
```

The internal model is therefore:

```text
Executor                 required
│
├── Suspendable          optional
│     ├── pause()
│     └── resume()
│
└── Cancelable           optional
      └── cancel()
```

Potential future protocols such as streaming/checkpointing should only be introduced when AgentDeck genuinely needs to control them.

Do not turn the executor system into a large framework interface prematurely.

---

# 10. Why `Suspendable` Instead of `Pausable` + `Resumable`

Separate protocols initially look flexible:

```python
Pausable
Resumable
```

But they model implementation mechanics rather than the lifecycle concept.

AgentDeck's public lifecycle is:

```text
RUNNING
   │ pause
   ▼
PAUSED
   │ resume
   ▼
RUNNING
```

So suspension is one coherent capability.

Use:

```python
Suspendable
```

with both operations.

If a runtime can stop but cannot later continue, that behavior should be modeled as something else, not as AgentDeck pause.

---

# 11. Capability → `run.can.*`

Executor capability and Run control availability are deliberately different layers.

Example:

```text
LangGraphExecutor
implements Suspendable
        │
        ▼
underlying capability exists
        +
Run.status == RUNNING
        +
AgentDeck policy allows pause
        │
        ▼
run.can.pause == True
```

Likewise:

```text
Suspendable + Run.status == PAUSED
→ run.can.resume == True
```

And:

```text
Cancelable + non-terminal Run
→ run.can.cancel == True
```

This gives AgentDeck a clean place to incorporate lifecycle state without polluting executor protocols.

---

# 12. Plain Python Example

A plain callable does not need to know anything about AgentDeck.

```python
async def load_customer(customer_id):
    ...
```

Usage:

```python
customer = await ctx.invoke(
    load_customer,
    customer_id,
)
```

Internally AgentDeck may resolve:

```text
load_customer
     ↓
CallableExecutor
     ↓
Run
```

The executor might support only execution and cancellation.

Then:

```python
run = ctx.invoke(load_customer, customer_id)

run.can.pause   # False
run.can.resume  # False
run.can.cancel  # perhaps True
```

No fake `pause()` implementation is required.

---

# 13. Rich Runtime Example

A LangGraph adapter may implement:

```python
class LangGraphExecutor(
    Executor,
    Suspendable,
    Cancelable,
):
    ...
```

Then:

```python
graph_run = ctx.invoke(graph, input)

graph_run.can.pause   # True while RUNNING
graph_run.can.resume  # False while RUNNING

await graph_run.pause()

graph_run.can.pause   # False
graph_run.can.resume  # True

await graph_run.resume()
```

The rest of the application uses AgentDeck semantics.

Framework-specific logic stays inside the executor/adapter.

---

# 14. `ctx.parallel()`

```python
results = await ctx.parallel(
    ctx.invoke(agent_a, input),
    ctx.invoke(agent_b, input),
    ctx.invoke(workflow, input),
)
```

`ctx.parallel()` is the composition primitive for coordinating child executions.

Conceptually:

```text
parent Run
│
├── child Run A
├── child Run B
└── child Run C
```

The targets do not need to share a framework.

Example:

```python
results = await ctx.parallel(
    ctx.invoke(openai_agent, question),
    ctx.invoke(langgraph_graph, question),
    ctx.invoke(search_function, question),
)
```

This is one of the most important consequences of the universal Run model:

> Composition happens at the execution layer, not at the framework layer.

---

# 15. Interaction APIs

AgentDeck-owned interaction stays on `ctx`.

```python
answer = await ctx.ask("Which environment?")
```

```python
approved = await ctx.approve(
    "Deploy to production?"
)
```

These suspend the **current execution branch** according to AgentDeck semantics.

This is fundamentally different from:

```python
await child.pause()
```

The distinction:

```text
ctx.ask()
ctx.approve()
    │
    └── suspend MY current branch for interaction

child.pause()
    │
    └── control ANOTHER child execution
```

This distinction is important enough to be part of the documented mental model.

---

# 16. Why Generic `ctx.pause()` Should Go Away

Under the final model, a generic:

```python
await ctx.pause()
```

becomes ambiguous.

Does it mean:

- pause the current underlying foreign runtime?
- suspend the current AgentDeck branch?
- pause until explicitly resumed?
- yield to pending external control?
- pause a child?

The API now has clearer concepts:

```python
await child.pause()       # control a child execution

await ctx.ask(...)        # suspend current branch for an answer
await ctx.approve(...)    # suspend current branch for approval

await ctx.safepoint()     # cooperate with runtime control
```

Therefore a generic `ctx.pause()` adds ambiguity rather than capability.

---

# 17. `ctx.safepoint()`

```python
await ctx.safepoint()
```

A safepoint is a cooperative boundary where AgentDeck may safely apply pending runtime control.

Example:

```python
async def process_dataset(ctx, items):
    for item in items:
        await process(item)
        await ctx.safepoint()
```

This enables AgentDeck-native functions/workflows to cooperate with lifecycle control without requiring them to implement a large pause/cancel interface.

The intended meaning is:

> "I have reached a point where AgentDeck may safely process pending execution control."

This should remain a low-level but explicit primitive.

---

# 18. Reporter

```python
ctx.reporter
```

The Reporter is the structured outward communication channel of the current execution.

It is not the Run lifecycle itself.

It is not merely an implementation of Python logging.

It is:

> **Information intentionally reported by running application/agent/workflow code, automatically associated with its execution origin.**

Baseline:

```python
ctx.reporter.info("Searching documents")

ctx.reporter.warning(
    "Primary source unavailable",
    source="drive",
)

ctx.reporter.error(
    "Index lookup failed",
    index="customers",
)
```

For structured/custom reports:

```python
ctx.reporter.report(
    "candidate_found",
    candidate=result,
    score=0.91,
)
```

---

## 18.1 Automatic execution association

The caller should not manually pass runtime identity.

AgentDeck knows where the report originated.

Conceptually:

```text
Report
├── run_id
├── execution/branch origin
├── timestamp
├── type / level
└── payload
```

So:

```python
ctx.reporter.info("Started validation")
```

is enough.

The current `ctx` supplies the execution association automatically.

---

## 18.2 Reporter vs events

Keep these distinct.

### Events

Events describe what happened in the AgentDeck runtime:

```text
run.started
run.completed
tool.started
child.started
approval.requested
run.failed
```

### Reporter

Reporter contains intentional information produced by the running software:

```text
"Searching 12 sources"
"Candidate found"
"Using fallback strategy"
"Validation confidence = 0.91"
```

They may ultimately flow through related infrastructure, but they are different concepts in the public API.

---

# 19. Agent Instances

Keep agent-instance operations grouped:

```python
ctx.agents.create(...)
ctx.agents.fork(...)
```

Current agent:

```python
ctx.agent
```

Example:

```python
worker = ctx.agents.create(worker_definition)

result = await ctx.invoke(
    worker,
    task,
)
```

Forking:

```python
copy = ctx.agents.fork(ctx.agent)

result = await ctx.invoke(
    copy,
    task,
)
```

This avoids making `ctx.invoke()` responsible for agent lifecycle construction.

---

# 20. `deck.runs.*` — Outside-Execution API

Inside an execution:

```python
ctx.invoke(...)
```

Outside an execution:

```python
run = deck.runs.start(target, input)
```

Operational surface:

```python
deck.runs.start(...)
deck.runs.get(...)
deck.runs.list(...)
deck.runs.events(...)
```

The returned Run should use the same lifecycle/control abstraction:

```python
run = deck.runs.start(agent, input)

run.status

run.can.pause
run.can.resume
run.can.cancel

await run.pause()
await run.resume()
await run.cancel()

result = await run
```

This means AgentDeck has one universal Run control vocabulary regardless of where the execution originated.

---

# 21. Full Real-World Example

Consider a ticket-resolution workflow using multiple runtime types.

```python
@workflow
async def resolve_ticket(ctx, ticket):
    customer = await ctx.invoke(
        load_customer,
        ticket.customer_id,
    )

    diagnosis = ctx.invoke(
        support_agent,
        {
            "ticket": ticket,
            "customer": customer,
        },
    )

    ctx.reporter.info(
        "Diagnosis started",
        ticket_id=ticket.id,
    )

    if diagnosis.can.pause:
        await diagnosis.pause()

        approved = await ctx.approve(
            "Continue automated diagnosis?"
        )

        if approved:
            await diagnosis.resume()
        else:
            await diagnosis.cancel()

    result = await diagnosis

    saved = await ctx.invoke(
        save_resolution,
        result,
    )

    ctx.reporter.info(
        "Ticket resolved",
        ticket_id=ticket.id,
    )

    return saved
```

The implementation may be:

```text
resolve_ticket              AgentDeck workflow Run
│
├── load_customer           plain Python child Run
│
├── support_agent           LangGraph/OpenAI/etc. child Run
│
├── approval                AgentDeck branch suspension
│
└── save_resolution         plain Python child Run
```

The workflow code is mostly unchanged if `support_agent` is replaced by another framework.

The executor changes.

The application execution model does not.

---

# 22. External UI Example

A production UI can inspect the same Run.

```python
run = await deck.runs.get(run_id)
```

UI logic:

```python
pause_button.enabled = run.can.pause
resume_button.enabled = run.can.resume
cancel_button.enabled = run.can.cancel
```

On click:

```python
await run.pause()
```

or:

```python
await run.cancel()
```

The UI does not need to know whether the underlying runtime is LangGraph, OpenAI Agents SDK, a custom workflow, or plain Python.

That is the practical meaning of AgentDeck's universal execution abstraction.

---

# 23. Internal Architecture

The implementation should preserve the simplicity of the public API.

```text
ctx.invoke(target)
        │
        ▼
InvocationService
        │
        ▼
InvocationResolver
        │
        ▼
Executor / adapter
        │
        ├───────────────► EventBridge
        │                     │
        ▼                     ▼
underlying runtime           Run
                              │
                ┌─────────────┼─────────────┐
                ▼             ▼             ▼
             Control        Store        Reporter
```

Responsibilities:

### InvocationResolver

Answers:

> "What is this object and which executor can run it?"

### Executor

Answers:

> "How is this target actually executed?"

### Run

Answers:

> "What is the AgentDeck identity, lifecycle and result of this execution?"

### Control

Answers:

> "Which lifecycle operations are valid and how are they applied?"

### EventBridge

Answers:

> "How are underlying/runtime events represented through AgentDeck?"

### Reporter

Answers:

> "What intentional structured information is this execution publishing?"

This separation prevents `ctx.invoke()` from becoming a god-function.

---

# 24. Why We Chose This Design

## 24.1 We want to run arbitrary agentic software

If every target had to implement:

```python
execute()
pause()
resume()
cancel()
events()
checkpoint()
...
```

then AgentDeck would only support frameworks that fully conform to AgentDeck.

That contradicts the goal.

Instead:

```text
execute      required
control      optional
```

This makes the system open-ended.

---

## 24.2 We do not want lowest-common-denominator Runs

The opposite mistake is giving every execution only the capabilities available everywhere.

That would make rich runtimes weaker merely because plain Python exists.

Capability protocols solve this:

```text
plain callable
→ Run with basic lifecycle

LangGraph
→ Run with richer suspension/control

future runtime
→ Run with whatever richer capabilities its executor exposes
```

---

## 24.3 We want one execution model across frameworks

The developer should compose:

```python
await ctx.invoke(x)
```

rather than learning one orchestration API per framework.

AgentDeck normalizes the **execution boundary**, not the internals of each framework.

This is a critical distinction.

---

## 24.4 We want the common case to remain tiny

The design still allows:

```python
result = await ctx.invoke(agent, input)
```

No explicit Run handling is necessary.

Advanced control is opt-in:

```python
run = ctx.invoke(agent, input)
await run.pause()
result = await run
```

This follows AgentDeck's governing principle:

> **We do the hard work; the user gets the short path.**

---

## 24.5 We want lifecycle control to be trustworthy

Operations such as:

```python
await run.pause()
await run.cancel()
```

should not silently fail.

Therefore strict methods are preferable to default `try_*` methods.

The separate:

```python
run.can.*
```

surface gives callers discoverability without weakening control semantics.

---

# 25. Alternatives Rejected

## 25.1 `try_pause()`

Example:

```python
ok = await run.try_pause()
```

Rejected as the primary API because:

- it duplicates lifecycle methods,
- boolean results are often underspecified,
- it encourages ignored failures,
- it does not eliminate state races,
- it makes control look less authoritative.

Strict operations plus `run.can.*` are cleaner.

---

## 25.2 `request_pause()`

This was considered for deferred/cooperative pause semantics.

It can model:

```text
APPLIED
PENDING
UNSUPPORTED
```

However, introducing request-based lifecycle APIs too early creates another semantic layer.

For now:

```python
run.can.pause
await run.pause()
ctx.safepoint()
```

is cleaner.

If AgentDeck later discovers a genuine need for asynchronous control requests, it can add such a concept intentionally rather than baking it into the baseline prematurely.

---

## 25.3 `Pausable` and `Resumable`

Rejected because pause/resume are one reversible lifecycle capability.

Use:

```python
Suspendable
```

instead.

---

## 25.4 Generic `ctx.pause()`

Rejected because it becomes ambiguous once:

- current branch suspension,
- child control,
- safepoints,
- external control,
- and framework-native suspension

all exist.

Clearer specialized operations already cover the real cases.

---

## 25.5 Full AgentDeck interface required for every target

Rejected because this would make "run anything" untrue.

Adapters/executors should normalize arbitrary targets at the boundary.

---

# 26. Canonical Public API

```text
ExecutionContext
│
├── data
├── reporter
├── agent
│
├── invoke()
├── parallel()
│
├── ask()
├── approve()
├── safepoint()
│
└── agents
    ├── create()
    └── fork()


Run
│
├── id
├── status
│
├── can
│   ├── pause
│   ├── resume
│   └── cancel
│
├── pause()
├── resume()
├── cancel()
│
├── events()
└── __await__()


Reporter
│
├── report()
├── info()
├── warning()
└── error()


Deck
└── runs
    ├── start()
    ├── get()
    ├── list()
    └── events()
```

Internal capability contracts:

```text
Executor
└── execute()

Optional:

Suspendable
├── pause()
└── resume()

Cancelable
└── cancel()
```

---

# 27. Canonical One-Sentence Definitions

### `ctx`

> The handle through which code inside an execution interacts with the AgentDeck runtime.

### `ctx.invoke()`

> Execute a target as an AgentDeck-managed child Run.

### `Run`

> AgentDeck's universal representation of an execution, including identity, lifecycle, observation, control, and result.

### `run.can.*`

> The lifecycle operations that are currently available for this Run.

### `Reporter`

> The structured outward reporting channel automatically associated with the current execution.

### `deck.runs.*`

> The operational API for finding, observing, starting, and controlling Runs from outside their execution.

### `Executor`

> The internal adapter contract that teaches AgentDeck how an arbitrary target executes.

### `Suspendable`

> Optional executor capability for reversible pause/resume control.

### `Cancelable`

> Optional executor capability for cancellation.

---

# 28. Final Architectural Principle

The complete design can be summarized as:

```text
Anything executable
        │
        ▼
   ctx.invoke()
        │
        ▼
      Run
        │
        ├── identity
        ├── lifecycle
        ├── events
        ├── reporting
        ├── composition
        └── capability-based control
```

AgentDeck does **not** need every runtime to behave identically.

It needs every execution to have a coherent AgentDeck identity and lifecycle, while allowing richer executors to progressively expose richer native control.

That gives AgentDeck a defensible role above individual agent frameworks:

> **We do not care how you built it. If it executes, AgentDeck can turn it into a Run, compose it with other executions, observe it, and control it to the extent its runtime safely allows.**

This is the final clean API direction.

---

# Appendix A — Native Definitions, Decorators, and Context Contracts

## A.1 Purpose

AgentDeck-native decorators define **what kind of executable a function is**, validate its contract when the Deck is built, and describe what the runtime must inject when that executable runs.

They do **not** own execution lifecycle.

The canonical flow remains:

```text
Definition
    ↓
ctx.invoke() / deck.runs.start()
    ↓
Executor
    ↓
Run
```

---

## A.2 Native Definition Types

```text
@tool       → ToolDefinition
@workflow   → WorkflowDefinition
Agent(...)  → AgentDefinition
```

These are definitions, not Runs.

Each invocation creates a new Run.

---

## A.3 `ToolCtx` vs `WorkflowCtx`

The intended relationship is:

```text
ToolCtx ⊂ WorkflowCtx
```

A tool is primarily a leaf capability.

A workflow is orchestration code.

### `ToolCtx`

Baseline:

```python
class ToolCtx:
    data
    reporter
    agent

    safepoint()
```

Example:

```python
@tool
async def search(
    ctx: ToolCtx,
    query: str,
) -> list[Result]:

    ctx.reporter.info(
        "Searching",
        query=query,
    )

    results = await backend.search(query)

    await ctx.safepoint()

    return results
```

A native tool can:

- access application context,
- report structured information,
- know the current agent when applicable,
- cooperate with runtime control through safepoints.

It does not receive orchestration APIs such as:

```python
ctx.invoke(...)
ctx.parallel(...)
ctx.ask(...)
ctx.approve(...)
```

This preserves the semantic rule:

```text
Tool = perform a capability
Workflow = coordinate executions
```

---

### `WorkflowCtx`

A workflow receives the richer orchestration surface:

```python
class WorkflowCtx:
    data
    reporter
    agent

    invoke(...)
    parallel(...)

    ask(...)
    approve(...)

    safepoint()

    agents.create(...)
    agents.fork(...)
```

Example:

```python
@workflow
async def research(
    ctx: WorkflowCtx,
    topic: str,
):

    sources = await ctx.parallel(
        ctx.invoke(search_web, topic),
        ctx.invoke(search_docs, topic),
    )

    approved = await ctx.approve(
        "Continue to synthesis?"
    )

    if not approved:
        return None

    return await ctx.invoke(
        writer_agent,
        sources,
    )
```

---

## A.4 What the Decorators Do

The decorators have three jobs:

```text
1. CLASSIFY
   What kind of executable is this?

2. VALIDATE
   Is its signature, context contract, and schema valid?

3. DESCRIBE
   What should the runtime inject and how should
   this executable be exposed/invoked?
```

They do not:

```text
create Runs
own lifecycle
pause/resume executions
manage sessions
execute themselves
```

---

## A.5 Build-Time Validation

Example native tool:

```python
@tool
async def search(
    ctx: ToolCtx,
    query: str,
    limit: int = 10,
) -> list[Result]:
    ...
```

AgentDeck can derive:

```text
ctx: ToolCtx        → runtime-injected dependency
query: str          → executable input
limit: int          → executable input
list[Result]        → output contract
```

Conceptually:

```text
ToolDefinition
├── name
├── description
├── input schema
├── output schema
├── required context: ToolCtx
└── callable
```

The injected context is not part of the external tool schema.

For example, the model-facing schema contains `query` and `limit`, but not `ctx`.

---

### Invalid context contract

This should fail during Deck/build validation:

```python
@tool
async def search(
    ctx: WorkflowCtx,
    query: str,
):
    ...
```

Because a tool must not silently acquire workflow orchestration capabilities.

Likewise:

```python
@workflow
async def research(
    ctx: ToolCtx,
    topic: str,
):
    ...
```

should fail because a workflow requires the workflow execution contract.

---

## A.6 Runtime Injection

Once a definition has passed validation, execution is straightforward.

For a tool:

```text
ctx.invoke(search)
        ↓
resolve ToolExecutor
        ↓
create child Run
        ↓
construct ToolCtx for that Run
        ↓
inject ToolCtx
        ↓
execute the underlying function
```

For a workflow:

```text
ctx.invoke(research)
        ↓
resolve WorkflowExecutor
        ↓
create child Run
        ↓
construct WorkflowCtx for that Run
        ↓
inject WorkflowCtx
        ↓
execute the workflow body
```

The developer never constructs `ToolCtx` or `WorkflowCtx` manually.

---

## A.7 Real Composition Example

```python
@tool
async def search_web(
    ctx: ToolCtx,
    query: str,
) -> list[str]:

    ctx.reporter.info(
        "Searching web",
        query=query,
    )

    return await web.search(query)


@workflow
async def research(
    ctx: WorkflowCtx,
    topic: str,
):

    web_run = ctx.invoke(
        search_web,
        query=topic,
    )

    docs_run = ctx.invoke(
        search_docs,
        query=topic,
    )

    sources = await ctx.parallel(
        web_run,
        docs_run,
    )

    approved = await ctx.approve(
        "Continue to synthesis?"
    )

    if not approved:
        return None

    return await ctx.invoke(
        writer_agent,
        sources,
    )
```

Execution tree:

```text
research                         Workflow Run
│
├── search_web                   Tool Run
│      └── ToolCtx
│
├── search_docs                  Tool Run
│      └── ToolCtx
│
├── approval                     WorkflowCtx suspension
│
└── writer_agent                 Agent Run
```

---

## A.8 Foreign Executables vs Native Definitions

A foreign executable can still be run:

```python
await ctx.invoke(existing_openai_tool, input)
await ctx.invoke(langgraph_graph, input)
```

AgentDeck can wrap it in a Run through an adapter.

But only AgentDeck-native definitions receive AgentDeck-native build validation and context injection.

```text
Foreign executable
    → AgentDeck can execute and wrap it as a Run

Native @tool / @workflow
    → AgentDeck can additionally validate the definition
      and inject ToolCtx / WorkflowCtx
```

This keeps AgentDeck interoperable without weakening the guarantees of its native API.

---

## A.9 Final Ruling

```text
@tool
  ↓
ToolDefinition
  ↓
validated against ToolCtx
  ↓
ToolExecutor
  ↓
Run


@workflow
  ↓
WorkflowDefinition
  ↓
validated against WorkflowCtx
  ↓
WorkflowExecutor
  ↓
Run
```

The decorators define and validate executable contracts.

The contexts define what runtime capabilities are available **inside** those executables.

The Executor performs the actual execution.

The Run remains the single lifecycle abstraction.

---

# Appendix B — Views and Observers

## B.1 Core Model

AgentDeck observability uses one simple pipeline:

```text
Event
  ↓
View
  ↓
Observer
```

Definitions:

```text
Event      = unified observation substrate
View       = declarative event selector
Observer   = event consumer
Reporter   = structured information emitted from execution
```

Views select events. Observers consume them.

The core intentionally does not model observer destinations, deduplication, destination identities, or observer registries beyond what is required to deliver events.

## B.2 Observer Contract

The required observer contract should stay minimal:

```python
class Observer(Protocol):
    async def emit(self, event: Event) -> None:
        ...
```

No additional concepts are required in the base protocol.

An observer instance is simply one consumer. Two observer instances mean two independent consumers. If both receive the same event, both may process it.

AgentDeck does not attempt to infer whether two observer configurations represent the same destination.

## B.3 Views

A View is a reusable predicate over the unified event stream:

```python
class View(Protocol):
    def matches(self, event: Event) -> bool:
        ...
```

Views compose declaratively:

```python
views.chat | views.tools
views.lifecycle | views.errors
views.reports & ~views.errors
~views.chat
```

Recommended built-ins:

```python
views.all
views.chat
views.tools
views.reports
views.lifecycle
views.errors
views.usage
```

## B.4 Public Usage

```python
from agentdeck import Deck, views
from agentdeck.observers import (
    ConsoleObserver,
    FileObserver,
    LangfuseObserver,
)

deck = Deck(
    observers=[
        ConsoleObserver(
            view=views.chat | views.reports,
        ),
        LangfuseObserver(
            view=views.all,
        ),
        FileObserver(
            "audit.jsonl",
            view=views.lifecycle | views.usage,
        ),
    ]
)
```

This is the complete public mental model.

## B.5 Multiple Observers

Multiple observers of the same type are valid:

```python
deck = Deck(
    observers=[
        ConsoleObserver(view=views.chat),
        ConsoleObserver(view=views.lifecycle),

        LangfuseObserver(
            project="production",
            view=views.all,
        ),
        LangfuseObserver(
            project="experiments",
            view=views.errors,
        ),
    ]
)
```

AgentDeck gives these no special meaning. They are simply independent consumers.

Even if two observers happen to point to the same external destination, destination uniqueness is not a core AgentDeck invariant.

## B.6 View Placement

Conceptually, a View belongs to an observer registration:

```text
Observer registration
├── observer
└── view
```

That internal distinction should not leak into the public API.

The ergonomic public form remains:

```python
ConsoleObserver(
    view=views.chat | views.tools,
)
```

No public `ObserverRegistration` or binding object is required.

## B.7 Reporter Relationship

Reporter output enters the same observation substrate:

```text
ctx.reporter.*
        │
        ▼
   report event
        │
Run / Tool / Agent / Workflow
        │
        ▼
      Event
        │
        ▼
       View
        │
        ▼
    Observer
```

Examples:

```python
ConsoleObserver(
    view=views.chat | views.reports,
)

LangfuseObserver(
    view=views.all,
)
```

Reporter remains conceptually distinct from lifecycle events even though both flow through the unified Event substrate.

## B.8 Final Ruling

```text
Observer = event consumer
View     = event selector
Reporter = structured information emitted from execution
Event    = unified observation substrate
```

The design intentionally excludes destination identity protocols, semantic duplicate detection, same-type restrictions, observer registry DSLs, and public observer bindings.

> Two observer objects simply mean two consumers.
