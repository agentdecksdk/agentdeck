
# Review — execution context and context injection plan

## Verdict

The core direction is correct:

> One application context value enters a run once, remains separate from workflow state and model context, and is available everywhere that participates in that execution.

I would approve the plan with several architectural changes before implementation.

The most important correction is to separate two responsibilities that the current plan treats as one:

1. **AgentDeck owns the public context contract and semantics.**
2. **The underlying engine should own context propagation when it already provides a mature mechanism for it.**

AgentDeck should therefore **not reimplement propagation through OpenAI Agents SDK or LangGraph**. Both engines already have first-class runtime-context facilities designed specifically for dependency injection.

OpenAI's Agents SDK passes an arbitrary application context through `Runner.run(..., context=...)` and exposes it to tools, dynamic instructions, hooks, handoffs, etc. via `RunContextWrapper[T]`; the SDK explicitly keeps that local context out of the model prompt.

LangGraph likewise has a first-class `context_schema`, invocation-level `context=`, and `Runtime[T]`, specifically separated from graph state and intended for things such as database connections, user information and runtime dependencies.

The architecture should use those capabilities rather than recreate them.

---

# 1. Split "context injection" into contract and transport

The current ruling says:

> Injection mechanism: our own, not the SDK's.

That is too broad.

There are actually two separate problems.

### Public contract

AgentDeck should own this:

```python
async def find_slots(
    date: str,
    ctx: Context[MiddleContext],
):
    ...
```

Users should not need to know whether that callable eventually runs under:

```python
RunContextWrapper
Runtime
SomeFutureEngineContext
```

That is an AgentDeck API concern.

### Runtime propagation

The engines should own this wherever possible.

For OpenAI:

```text
AgentDeck run
    ↓
Runner.run_streamed(context=...)
    ↓
OpenAI SDK propagates context
    ↓
tool / instructions / hooks / handoffs
```

For LangGraph:

```text
AgentDeck run
    ↓
graph.astream(..., context=...)
    ↓
LangGraph Runtime
    ↓
nodes / edges / tools
```

AgentDeck does not gain anything by manually reproducing those propagation mechanisms.

### Recommended ruling

Replace ruling #2 with:

> **AgentDeck owns the public `Context[T]` contract and injection semantics. Engine-native runtime-context facilities are used for propagation wherever available. Engine adapters bridge their native context wrapper to AgentDeck's `Context[T]`; they do not redefine its semantics.**

This distinction should be explicit throughout the plan.

---

# 2. Keep one AgentDeck `Context[T]` public API

Using the native engine contexts does **not** mean exposing them to AgentDeck users.

Without an AgentDeck abstraction, OpenAI code becomes:

```python
async def find_slots(
    ctx: RunContextWrapper[MiddleContext],
    date: str,
):
    ...
```

while a LangGraph node becomes:

```python
async def reserve(
    state: BookingState,
    runtime: Runtime[MiddleContext],
):
    ...
```

That would couple application code to the execution engine.

AgentDeck should keep:

```python
async def find_slots(
    date: str,
    ctx: Context[MiddleContext],
):
    ...
```

and:

```python
async def reserve(
    state: BookingState,
    ctx: Context[MiddleContext],
):
    ...
```

The exact same public type works everywhere.

The internal path becomes:

```text
                    Context[T]
              AgentDeck public contract
                         │
          ┌──────────────┴──────────────┐
          │                             │
    OpenAI adapter                LangGraph adapter
          │                             │
RunContextWrapper[...]              Runtime[...]
          │                             │
          └──── native transport ───────┘
```

So the correct principle is:

> **Uniform API above the engines; native propagation inside the engines.**

---

# 3. Do not free the OpenAI SDK `context=` slot

The plan currently proposes:

> Free the SDK's `context=` slot.

I would explicitly reverse this.

The slot is exactly what AgentDeck needs.

The current implementation in:

```text
agentdeck/adapters/engines/openai_agents/engine.py
```

already does:

```python
Runner.run_streamed(
    agent,
    message,
    context=ctx,
    ...
)
```

That is directionally correct.

Instead of removing it, evolve what `ctx` contains.

For v3, the existing internal `RunContext` can remain the object transported through the SDK:

```text
Runner
  context = RunContext
```

and `RunContext` gains:

```python
data: MiddleContext
```

The SDK then propagates that same internal execution object to all SDK execution sites.

OpenAI's SDK already supports local context across agents, tools, dynamic instructions and lifecycle hooks.

AgentDeck only needs a small bridge between:

```text
RunContextWrapper[RunContext]
```

and:

```text
Context[MiddleContext]
```

It should not replace the SDK's propagation machinery.

---

# 4. Replace LangGraph `configurable` context propagation with native runtime context

The current LangGraph engine uses:

```python
config["configurable"]["reporter"]
```

This was reasonable for reporter injection, but v3 application context should use LangGraph's actual runtime-context API.

LangGraph now explicitly separates:

```text
state
```

from:

```text
runtime context
```

State is mutable workflow data.

Runtime context is invocation-scoped information/dependencies such as DB clients, configuration, user information and other resources.

That is almost exactly the distinction AgentDeck wants:

```text
BookingState
    changes as workflow executes

MiddleContext
    environment supplied to the run
```

So instead of:

```python
config = {
    "configurable": {
        "thread_id": thread_id,
        "reporter": ctx.reporter,
        "agentdeck_context": ctx,
    }
}
```

the conceptual invocation should become:

```python
graph.astream(
    graph_input,
    config=config,
    context=ctx,
)
```

where `configurable` remains for genuine LangGraph configuration such as `thread_id`, while application/execution context travels through the native runtime-context channel.

LangGraph nodes natively receive this through `Runtime[T]`.

AgentDeck then bridges that `Runtime` into the user's `Context[T]`.

---

# 5. AgentDeck still needs a callable adapter/compiler

Using native propagation does **not** eliminate all AgentDeck machinery.

It eliminates the hard part: carrying the context correctly through an engine execution graph.

AgentDeck still needs to adapt the public callable signature.

User code:

```python
async def find_slots(
    date: str,
    ctx: Context[MiddleContext],
):
    ...
```

Conceptually, the OpenAI adapter can compile that into something equivalent to:

```python
@function_tool
async def sdk_find_slots(
    wrapper: RunContextWrapper[RunContext],
    date: str,
):
    return await find_slots(
        date=date,
        ctx=public_context(wrapper.context),
    )
```

The SDK handles:

* context propagation
* tool dispatch
* recognizing its own context parameter
* excluding that native context argument from the model-visible tool schema

AgentDeck handles:

* detecting `Context[T]`
* validating `T`
* producing the engine-specific bridge
* calling the original user callable

Likewise LangGraph conceptually receives:

```python
async def langgraph_reserve(
    state: BookingState,
    runtime: Runtime[RunContext],
):
    return await reserve(
        state,
        public_context(runtime.context),
    )
```

The important architectural boundary is:

```text
User callable
     ↓
AgentDeck callable analysis
     ↓
engine-specific bridge
     ↓
engine-native context propagation
```

This is much smaller and safer than AgentDeck implementing its own propagation system.

---

# 6. Plain Python callables should remain the canonical portable declaration

For portable AgentDeck components, prefer:

```python
async def find_slots(...):
    ...

Agent(
    tools=[find_slots],
)
```

rather than requiring users to decorate the function with an engine-specific decorator.

AgentDeck can then compile that callable into whatever the active engine requires.

This matters beyond context injection.

It gives AgentDeck a clean place to later add:

* tool permissions
* approvals
* retry policies
* telemetry
* UI metadata
* structured response metadata
* validation
* execution policies
* engine-specific compilation

Native engine objects should still be supported as escape hatches.

For example, an already-built OpenAI `FunctionTool` can be accepted, but then it should be considered:

> **engine-native**

rather than automatically receiving every AgentDeck portability guarantee.

The contract should distinguish:

```text
AgentDeck-managed callable
    full portable Context[T] support

native engine object
    engine-specific behavior
```

That will prevent `build()` from pretending it can introspect opaque native objects that it cannot actually understand.

---

# 7. Clarify what "nested execution inherits context" means

This sentence needs a precise execution-boundary rule:

> Nested execution inherits the caller's context by default.

There are two different cases.

## Same AgentDeck run

If a workflow invokes an agent or another component **without creating another canonical AgentDeck run**, it should receive the same context:

```text
run_id = R1
data = MiddleContext X
```

The same public `Context[T]` semantics apply throughout that execution.

## Actual child run

If AgentDeck starts a new canonical run, it must have its own identity.

For example:

```text
Parent
  run_id = R1
  data = X

Child
  run_id = R2
  parent_run_id = R1
  data = X
```

The application data should normally be **the exact same object reference**, but execution metadata belongs to the child.

Therefore the actual invariant should be:

> **Application context data inherits by reference by default. Execution identity and control metadata follow the AgentDeck run boundary.**

This will matter later for:

* cancellation
* checkpoints
* progress
* tracing
* deadlines
* budgets
* event attribution
* parent/child relationships

Do not define "nested execution" as blindly reusing the entire parent `Context` object.

---

# 8. Keep `RunContext` as the internal carrier for v3

The decision to defer the `Execution` split is reasonable.

Do not introduce another competing internal context container in the meantime.

Conceptually:

```python
RunContext
    tenant
    principal
    run_id
    trace_id
    session_id
    parent_run_id
    deadline
    budget

    gate
    reporter

    data
```

`data` is the exact application-owned object supplied to:

```python
deck.run(context=...)
```

Then public:

```python
Context[T]
```

is a restricted view over the current execution.

Conceptually:

```python
Context[T]
    data -> T
    reporter
    run_id
    session_id
    checkpoint()
```

The hierarchy should be:

```text
RunContext                internal
    │
    ├── execution metadata
    ├── control
    ├── reporting
    └── data ───────────── application-owned T
          │
          └── exposed through Context[T]
```

When the later `Execution` split occurs:

```text
RunContext → identity/value portion

Execution → live runtime portion + data
```

the public `Context[T]` does not need to change.

That validates the decision to defer the split.

---

# 9. Preserve the "context never automatically reaches the model" rule

This part of the original plan is important and should stay.

For:

```python
def instructions(ctx: Context[MiddleContext]) -> str:
    ...
```

only the returned string becomes model context.

AgentDeck must never automatically serialize:

```python
ctx.data
```

into the prompt.

This aligns with OpenAI's local-context semantics: objects passed through `Runner.run(context=...)` are explicitly local runtime data rather than LLM context.

Likewise LangGraph explicitly distinguishes runtime context from graph state and model-visible information.

The security rule should be written strongly:

> **Possessing `Context[T]` gives application code access to runtime dependencies; it does not implicitly grant the model access to those dependencies or their values. Only explicit user code may project context into model-visible instructions/input.**

This is one of the strongest parts of the design.

---

# 10. Keep annotation-based injection, but define the exact rules

The annotation rule is good:

```python
ctx: Context[T]
```

not:

```text
parameter named "ctx"
```

The plan should specify the full behavior.

### Zero `Context[...]` parameters

Normal callable:

```python
def foo(a: str):
    ...
```

### Exactly one

Injected:

```python
def foo(
    a: str,
    environment: Context[MiddleContext],
):
    ...
```

The name is irrelevant.

### More than one

Reject at build:

```python
def foo(
    a: Context[MiddleContext],
    b: Context[MiddleContext],
):
    ...
```

Example error:

```text
ConfigError:
foo declares multiple Context[...] parameters; at most one is allowed.
```

### Introspection

The implementation should account for:

```python
from __future__ import annotations
```

and wrapped callables.

Conceptually use:

```text
inspect.unwrap
inspect.signature
typing.get_type_hints
```

rather than trusting raw `__annotations__`.

If a decorator destroys the original signature/annotations, static validation may not be possible.

That should fall into the runtime safety-net path rather than trying to guess.

---

# 11. Be conservative about `Context[T]` build-time type checking

This part should be slightly weakened:

> Subtypes and protocol-compatible types are allowed.

Concrete runtime type compatibility is reasonable.

For example:

```python
class BaseContext: ...

class MiddleContext(BaseContext): ...
```

A deck declaring:

```python
Deck(context=MiddleContext)
```

can satisfy:

```python
Context[BaseContext]
```

Likewise:

```python
Context[Any]
```

can always be accepted.

But arbitrary structural `Protocol` compatibility cannot reliably be proven at runtime with the same guarantees as a static type checker.

Therefore I would define v3 as:

* exact concrete type → supported
* subtype → supported
* `Any` → supported
* supported runtime ABCs such as `Mapping` → where Python runtime semantics make the check meaningful
* arbitrary structural `Protocol` → best effort or deferred

Do not turn `Deck.build()` into a partial mypy implementation.

Recommended wording:

> **Build-time compatibility guarantees apply to runtime-introspectable types. Structural protocols may be accepted where runtime compatibility can be established; otherwise validation is deferred to invocation time.**

That preserves future flexibility without overpromising.

---

# 12. Keep workflow state and execution context strictly separate

This rule is correct and should remain central.

```python
async def reserve(
    state: BookingState,
    ctx: Context[MiddleContext],
):
    ...
```

means:

```text
state
    workflow-owned mutable data

ctx.data
    application-owned runtime environment
```

Neither should absorb the other.

Do not put:

```python
db
calendar client
authenticated principal
service handles
```

into graph state just because nodes need them.

And do not use runtime context as an alternative mutable workflow state.

LangGraph itself now makes essentially the same distinction between mutable state and static invocation context.

This alignment is another reason to use LangGraph's native context mechanism.

---

# 13. Make a firm sandboxed-skill ruling for v3

I would resolve the open skills question now:

> **A skill requiring live `Context[T]` must execute in-process. A sandboxed skill cannot receive live `Context[T]` in v3.**

Reason:

`MiddleContext` may contain:

```python
db
calendar_client
http_client
transaction
credentials
service handles
```

These may be non-serializable and may also represent capabilities that should not silently cross a process boundary.

Therefore:

```text
skill declares Context[T]
       +
skill sandbox=True
       ↓
ConfigError
```

Example:

```text
SkillContextError:
skill 'booking' requires Context[MiddleContext] but is configured
for sandboxed execution. Live execution context cannot cross the
sandbox boundary.
```

Later, AgentDeck can deliberately introduce:

```text
context projection
```

for example:

```python
Skill(
    sandbox=True,
    context_projection=project_skill_context,
)
```

which produces a specifically serializable capability-safe object.

But this should be an explicit future feature.

Never automatically:

```python
json.dumps(ctx.data)
```

or:

```python
ctx.data.model_dump()
```

across a sandbox boundary.

The fact that something is serializable does not imply that it is appropriate to expose to an isolated process.

---

# 14. Define `namespace` before exposing it

The proposed public context contains:

```python
ctx.namespace
```

but the current `RunContext` does not have a clearly corresponding concept.

Possible meanings include:

* tenant namespace
* deck namespace
* resource-resolution namespace
* current component namespace
* event namespace

These are not equivalent.

Unless one stable definition can be written now, remove it from the initial public API.

Start with:

```python
ctx.data
ctx.reporter
ctx.run_id
ctx.session_id
await ctx.checkpoint()
```

and add namespace later when a concrete requirement exists.

Adding a property later is easy.

Changing its semantics after public release is harder.

Also fix the document's "five members" wording depending on the final surface.

---

# 15. Native engine capabilities should not leak into `Context[T]` automatically

Both underlying engines expose additional runtime information.

For example, OpenAI's wrapper contains SDK usage information and specialized tool contexts.

Current LangGraph `Runtime` can expose store, stream writer, execution information and other runtime facilities.

Do not immediately mirror all of those onto:

```python
Context[T]
```

AgentDeck should expose only capabilities that have **AgentDeck-level semantics**.

For example:

```python
ctx.reporter
ctx.checkpoint()
```

make sense because AgentDeck defines what progress/events/control mean independently of the engine.

Something like:

```python
ctx.langgraph_store
```

obviously should not exist.

Likewise SDK-specific tool call metadata should remain available only through an explicit native escape hatch if needed.

The public context should represent the stable intersection of **AgentDeck concepts**, not the union of every engine's runtime API.

---

# 16. Be explicit about opaque/native components during `build()`

The plan says `build()` walks:

* agents
* instruction callables
* tools
* workflow steps
* nodes

That is valid for AgentDeck-managed definitions.

It may not be valid for arbitrary engine-native components.

Example:

```text
prebuilt SDK FunctionTool
precompiled/custom wrapped LangGraph node
opaque callable object
decorator that discarded annotations
```

AgentDeck may not be able to recover the original `Context[T]` requirement.

Therefore define two validation levels:

### AgentDeck-managed component

Full static validation:

```text
Context requirement known
schema known
compatibility checked at build()
```

### Opaque/native component

Best-effort build validation plus invocation-time safety net.

Do not claim every possible native object can be statically understood.

That matches the existing risk section but should be elevated into the actual contract.

---

# 17. Recommended internal flow

The final model should look approximately like this:

```text
deck.run(context=middle_context)
          │
          ▼
    RunContext
    ├─ run identity
    ├─ gate
    ├─ reporter
    └─ data ──────► middle_context
          │
          │
          ├─────────────────────────────┐
          ▼                             ▼
 OpenAI Agents                    LangGraph
 context=RunContext               context=RunContext
          │                             │
 RunContextWrapper                 Runtime
          │                             │
 AgentDeck bridge                  AgentDeck bridge
          │                             │
          └─────────────┬───────────────┘
                        ▼
                Context[MiddleContext]
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
      tool         workflow node      hooks/
                                    instructions
```

The crucial property is that only one application object exists:

```text
middle_context
```

It is not copied.

It is not serialized.

It is not converted into workflow state.

It is not automatically sent to the model.

The engine-native context machinery transports the execution carrier, while AgentDeck presents one stable public view.

---

# 18. Recommended revised rulings

I would update the ruling table to approximately:

| Question                                  | Ruling                                                                                                                                                                                                                         |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `Execution` split from `RunContext`   | **Yes, deferred.** Add application `data` to today's internal execution carrier and preserve the public `Context[T]` contract so the later split is internal only.                                                   |
| Who owns context semantics?               | **AgentDeck.** `Context[T]` is the only portable public context API.                                                                                                                                                   |
| How is context propagated inside engines? | **Use native runtime-context facilities wherever available.** OpenAI uses `Runner(..., context=...)`; LangGraph uses runtime `context=` / `Runtime[T]`. AgentDeck bridges those native wrappers to `Context[T]`. |
| Injection detection                       | **Annotation-based.** Exactly one `Context[T]` parameter may be injected; names are irrelevant.                                                                                                                        |
| Deck context declaration                  | **Yes.** `Deck(context=MiddleContext)` defines the application context type and enables build-time compatibility checks.                                                                                               |
| Workflow state relationship               | **Strictly separate.** State is mutable workflow data; `ctx.data` is application/runtime dependency data.                                                                                                              |
| Nested execution                          | **Same run → same execution context. Child run → same `data` reference by default, new execution identity.**                                                                                                         |
| Sandboxed skills                          | **No live `Context[T]` in v3.** A skill requiring it must run in-process; explicit serializable projections can be added later.                                                                                        |
| Native/opaque components                  | **Supported with weaker static guarantees.** Invocation-time validation remains mandatory.                                                                                                                               |

---

# 19. Recommended sequencing

I would change the implementation order to:

### 1. Public `Context[T]` + internal `RunContext.data`

Define the stable public surface first.

No engine changes yet.

### 2. Callable introspection

Implement one engine-independent analyzer that determines:

```text
original callable
Context parameter
required T
visible parameters
whether static inspection is reliable
```

### 3. Callable bridge/compiler abstraction

Create the machinery that transforms an AgentDeck callable into an engine-native callable while retaining metadata about the original function.

### 4. OpenAI Agents integration

Keep:

```python
Runner.run_streamed(..., context=ctx)
```

Use SDK-native context propagation.

Compile AgentDeck tools/instructions/hooks into SDK-native wrappers.

Do **not** reimplement SDK propagation.

### 5. LangGraph integration

Move runtime application context from `configurable` to native LangGraph `context=` / `Runtime[T]`.

Keep `configurable` only for things that are actually LangGraph configuration, such as the thread ID.

Bridge AgentDeck workflow node signatures to native LangGraph nodes.

### 6. Dynamic instructions and hooks

Apply the same callable compiler instead of creating separate injection systems.

### 7. `Deck.build()` graph validation

Walk all AgentDeck-managed components and validate their context requirements against:

```python
Deck(context=...)
```

Keep invocation-time validation for opaque/native components.

### 8. Skills

Add in-process `Context[T]`.

Reject sandbox + live context.

Design serializable projections separately later.

---

# Final architectural rule

The plan should ultimately be governed by this:

> **One application context value enters AgentDeck at the run boundary. AgentDeck owns its public semantics through `Context[T]` and keeps it separate from workflow state and model context. The active engine's native runtime-context mechanism transports the execution carrier wherever possible, while thin AgentDeck bridges adapt native wrappers to the portable `Context[T]` API. The application value is never implicitly copied, serialized, prompted, or manually forwarded between components.**

That gives AgentDeck the abstraction it needs without duplicating functionality that OpenAI Agents SDK and LangGraph already implement well.

It also preserves the reason for having AgentDeck in the first place: application code depends on **AgentDeck concepts**, while engine-specific runtime details remain behind adapters.
