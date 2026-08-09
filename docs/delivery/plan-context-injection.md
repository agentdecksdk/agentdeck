# Plan — execution context and context injection

**Status:** proposed · **Date:** 2026-08-09 · **Target: v3.0.0**
Revised against `review-context-injection.md`, which reversed the original ruling on injection.
Pairs with `plan-phase4-deck.md`. The rule: **one context value enters the run once, and
AgentDeck owns its public semantics across the whole execution graph.**

## Rulings

| # | Question | Ruling |
|---|---|---|
| 1 | Who owns context *semantics*? | **AgentDeck.** `Context[T]` is the only portable public context API; application code never names an engine type. |
| 2 | Who owns context *propagation*? | **The engines.** Both already have first-class runtime-context facilities built for this. AgentDeck bridges them to `Context[T]`; it does not reimplement them. |
| 3 | `Execution` split from `RunContext` | **Yes, deferred.** Add `data` to today's internal carrier; the public `Context[T]` does not change when the split happens, which is what makes deferring it safe. |
| 4 | Injection detection | **Annotation-based.** Exactly one `Context[...]` parameter; the name is irrelevant; two is a `build()` error. |
| 5 | Deck declares the context type | **Yes** — `Deck(context=MiddleContext)`, enabling build-time compatibility checks. |
| 6 | Nested execution | **Same run → same execution context. Child run → same `data` by reference, new execution identity.** |
| 7 | Workflow state vs context | **Strictly separate.** `state` is workflow-owned mutable data; `ctx.data` is the application-owned environment. Neither absorbs the other. |
| 8 | **Sandboxing** | **A future ability, currently disabled and out of scope (#163).** No component — agent, tool or skill — is sandboxed in v3, so no context crosses a process boundary and the projection question does not arise. |
| 9 | Native/opaque components | **Supported, with weaker static guarantees.** Invocation-time validation stays mandatory. |
| 10 | `context=None` when a type is declared | Every root whose graph requires context must receive a compatible instance, checked **before execution**. A context-free root may run with `None`. `resume()` applies the same rule. |

## The split that matters

Two responsibilities the first draft conflated:

```
        Context[T]                    ← AgentDeck owns this (public contract)
             │
   ┌─────────┴─────────┐
   ▼                   ▼
OpenAI adapter    LangGraph adapter   ← thin bridges
   │                   │
RunContextWrapper   Runtime[T]        ← the engines own this (transport)
```

Both SDKs already carry an arbitrary application object through an execution and hand it to
tools, instructions and hooks — and both explicitly keep it *out* of the model prompt.
Reimplementing that takes on the hard half of the problem for no gain. What AgentDeck keeps is
the part the engines cannot do: one portable public type, so a tool signature does not change
when the engine does.

## The public surface

```python
ctx: Context[MiddleContext]

ctx.data          # the value passed to deck.run(context=...)
ctx.reporter      # progress/status
ctx.run_id · ctx.session_id
await ctx.checkpoint()
```

No `ToolContext`, `SkillContext`, `WorkflowContext`, `AgentContext`. `Gate` stays internal,
reached only through `checkpoint()`.

**`ctx.namespace` is deliberately not in the initial surface.** `RunContext.namespace` is defined
and load-bearing for storage isolation, but no injection site has needed to *read* it yet, and a
property added later is cheaper than one whose meaning changes after release.

**Nothing from the engines is mirrored automatically.** `ctx.reporter` and `ctx.checkpoint()`
exist because AgentDeck defines what progress and control mean independently of the engine. SDK
usage metadata, LangGraph's store or stream writer and the like do not belong here — the public
context is the stable intersection of AgentDeck concepts, not the union of two runtime APIs.

## Injection is annotation-based, never name-based

```python
@function_tool
async def find_slots(date: str, environment: Context[MiddleContext]):
    return await environment.data.calendar.find_slots(date=date)
```

Injected because it is annotated `Context[...]`, not because of the parameter's name.

- **Zero** — an ordinary callable, nothing injected.
- **Exactly one** — injected, whatever it is called.
- **More than one** — `build()` error: *`foo` declares multiple `Context[...]` parameters; at most one is allowed.*

Introspection uses `inspect.unwrap`, `inspect.signature` and `typing.get_type_hints` rather than
raw `__annotations__`, so `from __future__ import annotations` and wrapped callables work. A
decorator that destroys the signature cannot be validated statically; that falls to the
invocation-time safety net rather than to a guess.

## What AgentDeck still builds

Native propagation removes the hard part, not all of it. AgentDeck compiles a user callable into
an engine-native one, keeping metadata about the original:

```
user callable → callable analysis → engine-specific bridge → engine-native propagation
```

Conceptually, for the SDK:

```python
@function_tool
async def sdk_find_slots(wrapper: RunContextWrapper[RunContext], date: str):
    return await find_slots(date=date, ctx=public_context(wrapper.context))
```

The SDK handles propagation, dispatch, and excluding its own context parameter from the
model-visible schema. AgentDeck handles detecting `Context[T]`, validating `T`, producing the
bridge, and calling the original.

**Plain callables are the canonical declaration.** `Agent(tools=[find_slots])` with an
undecorated function; AgentDeck compiles it for whichever engine is active. That is also the
natural home for permissions, approvals, retries and telemetry later — none of them v3. A
pre-built engine-native object is still accepted, but is **engine-native**: it gets no
portability guarantee, and `build()` does not pretend it can introspect it.

## Engine integration

**OpenAI Agents.** Keep `Runner.run_streamed(..., context=ctx)`. The slot is exactly what is
needed, and the earlier plan's "free the slot" was backwards. `RunContext` stays what travels;
it gains `data`.

**LangGraph.** Move application context off `configurable` onto the native runtime-context
channel (`context=` / `Runtime[T]`). `configurable` keeps what is genuinely LangGraph
configuration — `thread_id` — and nothing else. LangGraph draws the same state-vs-runtime-context
line AgentDeck wants, which is a second reason to use it rather than a parallel mechanism.

## Type compatibility, conservatively

```
ContextTypeError:
find_slots requires MiddleContext, but this deck provides GitHubContext.
```

| | |
|---|---|
| exact concrete type | supported |
| subtype | supported |
| `Any` | supported |
| runtime ABCs (`Mapping`, …) | where the runtime check is meaningful |
| arbitrary structural `Protocol` | best effort, else deferred to invocation |

> Build-time guarantees apply to runtime-introspectable types. Structural protocols are accepted
> where runtime compatibility can be established; otherwise validation is deferred.

`build()` is not a partial type checker and should not grow into one. Dicts are never
auto-converted into typed models — `Context[Mapping[str, Any]]` is first-class, and the
conversion is the application's to write.

### Two validation levels

**AgentDeck-managed** — requirement known, schema known, checked at `build()`.
**Opaque or engine-native** — best effort at build, invocation-time safety net mandatory. The
contract says this rather than implying every native object is introspectable.

## The rule that must not be weakened

> **Possessing `Context[T]` gives application code access to runtime dependencies. It does not
> grant the model access to them or their values. Only explicit user code may project context
> into model-visible instructions or input.**

`def instructions(ctx) -> str` puts *only its return value* in the prompt. `ctx.data` is never
serialized into it. Both engines already treat their runtime context as local rather than model
context; this states it as an AgentDeck guarantee rather than an inherited accident.

## Lifecycle

Run-scoped and application-owned. AgentDeck keeps the same value for the whole run, never
serializes it into the event log, never clones it, and assumes nothing about it being
serializable. That is also why it is not on `RunContextSnapshot`: the log records what a run was
asked to do, not the live objects it held. `resume()` resupplies it.

## Sequencing

1. Public `Context[T]` + `RunContext.data` — no engine changes
2. Callable analysis: the `Context` parameter, required `T`, visible parameters, whether static inspection is reliable
3. The callable bridge/compiler abstraction
4. OpenAI Agents: compile tools/instructions/hooks into SDK-native wrappers over the existing `context=`
5. LangGraph: application context onto `Runtime[T]`; `configurable` keeps `thread_id`
6. Dynamic instructions and hooks through the same compiler — not a second injection system
7. `Deck.build()` graph validation and `ContextTypeError`
8. Skills — in-process only, since sandboxing is disabled (ruling 8)

## Risks

- **Two bridges, one contract.** The uniformity users see is produced by two adapters that must
  agree. A contract test parametrized over both engines is the only thing keeping them honest.
- **`build()` validation depends on introspectable signatures.** The safety net is not optional.
- **The compiler is where scope creep arrives.** Permissions, retries and telemetry all have a
  natural home there; none are v3.
