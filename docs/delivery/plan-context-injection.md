# Plan — execution context and context injection

**Status:** proposed · **Date:** 2026-08-09 · **Target: v3.0.0**
Pairs with `plan-phase4-deck.md`. The rule: **one context value enters the run once, and
AgentDeck owns propagation and injection across the whole execution graph.**

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | `Execution` split out of `RunContext` | **Yes, but deferred.** Ship injection on today's `RunContext`; do the split later as its own change. |
| 2 | Injection mechanism | **Our own**, not the SDK's — so one mechanism covers every engine and every site: agent, tool, skill, workflow step. |
| 3 | Sandboxed skills | **The subprocess becomes optional**, a per-skill choice AgentDeck supplies. How context reaches a skill is **still open** (below). |
| 4 | Deck declares the context type | **Yes** — `Deck(context=MiddleContext)`, validated at `build()`. |

## The public surface — one generic, five members

```python
ctx: Context[MiddleContext]

ctx.data          # the value the caller passed to deck.run(context=...)
ctx.reporter      # progress/status, already exists
ctx.run_id · ctx.session_id · ctx.namespace
await ctx.checkpoint()        # delegates to the internal Gate
```

No `ToolContext`, `SkillContext`, `WorkflowContext`, `AgentContext`. One type, everywhere.
`Gate` stays internal and is reached only through `checkpoint()`.

## Injection is annotation-based, never name-based

A parameter is injected because it is annotated `Context[T]` — **not** because it is called
`ctx`. A user may name it anything.

```python
@function_tool
async def find_slots(date: str, ctx: Context[MiddleContext]):
    return await ctx.data.calendar.find_slots(business=ctx.data.business, date=date)
```

The model sees `{"date": str}` only. AgentDeck strips the `Context[...]` parameter from the tool
schema it publishes and supplies it at dispatch.

**Why our own mechanism and not the SDK's** (ruling 2): the Agents SDK does exactly this for
`RunContextWrapper[T]`, but only for SDK function tools. LangGraph nodes, workflow steps and
skills get nothing from it. One mechanism that reads a signature, strips the parameter, and
injects at call time works identically at all four sites — and it frees the SDK's `context=`
slot, which `RunContext` occupies today (`adapters/engines/openai_agents/engine.py`).

## The four injection sites

**Dynamic instructions.** `def instructions(ctx: Context[T]) -> str`, called with the live
context before the turn. *Only what the function returns reaches the prompt* — the context is
never dumped into it wholesale. That is a security property, not a convenience.

**Function tools.** As above; schema-stripped, injected at dispatch.

**Workflow steps.** `async def reserve(state: BookingState, ctx: Context[T])`. `state` is what
the workflow produces; `ctx.data` is the environment it was given. Neither becomes the other.

**Agent hooks.** `async def on_start(ctx: Context[T])` — same type, no separate model.

**Nested execution** inherits the caller's context by default. No `run_child(context=ctx.data)`
per call. Explicit override is a later addition, not a v3 requirement.

## Type compatibility

Declared at the deck, checked at `build()`, enforced at `run()`:

```
ContextTypeError:
find_slots requires MiddleContext, but this deck provides GitHubContext.
```

- **Subtypes and protocol-compatible types are allowed.** A component asking for a `Protocol` the
  supplied type satisfies is valid.
- `Context[Any]` and `Context[Mapping[str, Any]]` are legal when asked for explicitly, so a plain
  `dict` context is a first-class choice.
- Dicts are **never** auto-converted into typed models. Typed dataclasses/pydantic remain the
  preferred path, but the conversion is the application's to write.

`build()` walks agents, their instruction callables, their tools, workflow steps and nodes, and
fails on the first incompatibility — before any model call. Invocation-time validation stays as
a safety net for anything the graph could not see statically.

## Lifecycle

The context is **run-scoped and application-owned**. AgentDeck:

- keeps the same value for the whole run
- never serializes it into the event log
- never clones it
- assumes nothing about it being serializable — DB clients and service handles are expected

On resume the application resupplies it: `await deck.resume(run_id, context=await resolve(...))`.
Provider-based reconstruction can be layered on later; it must build on this contract rather
than replace it.

This is also why the context is not on `RunContextSnapshot`: the log records what a run was asked
to do, not the live objects it was handed.

## Where it rides internally (until the Execution split)

A fourth internal field on `RunContext`, beside `gate` and `reporter`. `RunContext` is already
internal and never constructed by a user, so nothing leaks. When ruling 1's split happens, that
field moves to `Execution` and `Context[T]` — the only thing user code ever touches — does not
change.

## Still open — needs a ruling before the skills work starts

**How does context reach a skill?** You said the subprocess should become optional and that
injection there needs thought. The two halves:

- **In-process skills** are straightforward: a real `Context[T]`, same as a tool.
- **Sandboxed skills** cannot receive a live object across the process boundary. The candidates
  are: nothing at all; a serializable projection the context type declares; or the skill simply
  cannot be sandboxed if it declares a `Context[...]` parameter, which turns the choice into a
  consequence of what the skill asks for rather than a separate flag.

The third reads best — the skill's own signature decides whether it can be isolated — but it is
not decided, and it interacts with making the sandbox optional at all.

## Sequencing

1. `Context[T]` and the signature/injection machinery (engine-agnostic, no engine changes yet)
2. Free the SDK's `context=` slot; inject at the openai-agents tool site
3. LangGraph node/step injection via `configurable`, beside the reporter
4. Instruction callables and agent hooks
5. `build()` graph validation and `ContextTypeError`
6. Skills — after the open ruling above

Steps 1–5 are independent of the skills question and can land while it is open.

## Risks

- **Schema stripping is the sharp edge.** A `Context[...]` parameter that reaches the published
  tool schema becomes a field the model tries to fill. That is the one failure worth a dedicated
  test at every site, not just at the SDK one.
- **Doing our own injection means tracking two engines' calling conventions.** The SDK gives its
  version away free; ruling 2 accepts that cost deliberately for uniformity, and it should be a
  judgment-ledger entry rather than a surprise in review.
- **`build()` validation depends on introspectable signatures.** A tool built dynamically, or one
  wrapped by a decorator that discards annotations, cannot be checked statically — the safety net
  has to stay.
