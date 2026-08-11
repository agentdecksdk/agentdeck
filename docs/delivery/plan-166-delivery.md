# Delivery plan — #166 `Context[T]` injection

**Status:** proposed · **Date:** 2026-08-11 · **Baseline:** `dev` at `659f64d`
Delivery companion to `docs/delivery/plan-context-injection.md`, which holds the design and its
ten rulings. That document is dated 2026-08-09 and predates `#164` (Deck), `#182`, `#172`, `#203`,
`#205` and `#155`. This one does not re-decide anything it settled; it records **what moved under
it**, and slices the work.

#166 is the last large feature in v3 (`roadmap-v3.md`, Ruling 3). Everything after it is
correctness, cleanup and observability.

## What changed under the design plan

Verified against the tree, not the issues.

### 1. The OpenAI transport already exists — step 4 is smaller than it reads

`adapters/engines/openai_agents/engine.py:185` already passes the run's context through the SDK's
own slot:

```python
Runner.run_streamed(
    agent,
    message,
    # The run context travels as the SDK's own context object, which is the one thing
    # the SDK hands a function tool: a tool declaring ``RunContextWrapper[RunContext]``
    # reaches ``wrapper.context.reporter`` (and the gate) without importing a Runtime.
    context=ctx,
```

So the plan's "keep `Runner.run_streamed(..., context=ctx)`" is not a change — it is already the
code, deliberately, with the reasoning in the comment. What step 4 actually adds is the **bridge**:
turning a user callable's `Context[T]` parameter into `wrapper.context`. Propagation is done.

### 2. `RunContext` has a docstring that argues against `data` — and it has to be answered

`core/context.py:32-36`:

> Three values and two seams, and nothing else: a field AgentDeck's own machinery never reads is
> not infrastructure, it is a guess about a mechanism that does not exist yet. `trace_id`,
> `budget`, `triggered_by`, `parent_run_id`, `deadline` and `idempotency_key` were all of that,
> and each comes back with the thing that enforces it.

`data` passes that test — the bridges read it, and it arrives *with* the mechanism that consumes
it. But the docstring is a standing rule, and adding a field without amending it leaves the next
reader with a rule the code visibly breaks. Slice 1 updates that paragraph in the same commit.

Note also that the class is `frozen=True, slots=True`, and its docstring says "Deliberately holds
no application identity". `data` is application-*owned* but not application-*identity* — the
distinction is worth stating there, since `namespace` already carries a careful "says which runs
are kept apart, never who is acting" caveat that `data` deliberately does not inherit.

### 3. LangGraph's `configurable` carries three keys, and one of them is there on purpose

The plan says `configurable` should keep "`thread_id` — and nothing else". Today
(`adapters/engines/langgraph/engine.py:72-92, 188-190`) it carries `thread_id`,
`agentdeck_stream`, and `reporter` — and `REPORTER_KEY`'s docstring defends the last one:

> langgraph's own injection channel, used rather than a channel of our own: a node that declares a
> `config: RunnableConfig` parameter reaches the reporter there, so reporting costs the graph
> schema nothing […] Non-scalar `configurable` values are excluded from checkpoint metadata by
> langgraph itself, so a durable graph does not try to serialize it.

Moving application context onto `Runtime[T]` is uncontroversial. Moving **`reporter`** there is a
behavior change for any workflow reading `config["configurable"]["reporter"]`, against a
documented decision. **Needs a ruling** — see below.

### 4. #166 reverses a guardrail #172 just shipped

`authoring/compile.py:110-115` rejects a bare callable in `tools=`:

```
agent {name!r} has a tool that is not an Agents SDK tool object: {tool!r}.
Wrap a plain function with @function_tool (from `agents`) before passing it …
```

That is #172, landed in Wave B, and it was right: a plain callable was accepted and then failed
obscurely. But the design plan says the opposite —

> **Plain callables are the canonical declaration.** `Agent(tools=[find_slots])` with an
> undecorated function; AgentDeck compiles it for whichever engine is active.

— because a callable annotated `Context[T]` *cannot* be pre-decorated: `@function_tool` would put
the context parameter in the model-visible schema. So #166 must turn the rejection into
compilation. This is not a conflict in intent (both want "a plain callable does not fail
obscurely"), but it is the same lines of code, and the error message, the docs that quote it
(#179's three corrected documents), and the test that asserts it all move together.

**Whoever implements slice 2 owns removing that guardrail, not working around it.**

## Slices

Four PRs in sequence. The rule that sets the boundaries: **a public knob turns on only in the
slice where it works** — #182 deleted `Deck(context=...)` precisely because accepted-then-refused
is a false promise, and re-creating that trap one slice early would be the same bug in a new place.

| PR | Sequencing steps | Ships | Public surface turned on |
|---|---|---|---|
| **1** | 1–2 | `Context[T]`, `RunContext.data`, callable analysis | **none** — internals only |
| **2** | 3–4 | the compiler + the OpenAI bridge, end to end on one engine | `run(context=)`, `Context` exported |
| **3** | 5–6 | LangGraph bridge; dynamic instructions and hooks | LangGraph parity |
| **4** | 7–8 | `Deck(context=T)`, build-time graph validation, `ContextTypeError`, skills, docs | `Deck(context=)` |

Steps 3 and 4 are deliberately **not** split. Shipping the compiler abstraction with no engine
consuming it is an interface with one implementation and no way to know whether its shape is
right — the thing both CLAUDE.md files forbid.

Per-slice notes:

- **Slice 1.** Callable analysis is pure stdlib introspection (`inspect.unwrap`,
  `inspect.signature`, `typing.get_type_hints`), which does not by itself make `core/` the right
  home — `core/` is the event schema and the ports. Decide and justify. The edge cases that
  actually bite: `from __future__ import annotations`, a `functools.wraps` wrapper, a decorator
  that destroys the signature (must report *unreliable*, never guess), zero/one/two `Context[...]`
  parameters, method vs function.
- **Slice 2.** Owns the `compile.py` guardrail reversal (§4 above) and every document that quotes
  its message. Also the first place the plan's "the model never sees `ctx.data`" guarantee becomes
  testable — assert the context parameter is absent from the generated tool schema, not just that
  the call works.

  **`reliable=False` must be settled on slice 2's first day** — added after slice 1, which
  surfaced it. `analyze_callable` reports honestly whether static inspection could be trusted, but
  slice 1 has nowhere to hand that answer; the invocation-time safety net is step 3's, which is
  slice 2. The failure mode to design against: a signature-destroying decorator makes an
  unreliable analysis fall through to the static path and read as *"no context declared"*, so the
  argument silently goes missing and the tool runs without its context. Unreliable must never
  default to "no context" — it must reach the runtime check, or refuse.

  Slice 1 also read a bare `Context` (no type argument) as `Context[Any]`, since none of the three
  documents cover it. The alternative — leaving it unparameterised — hands an AgentDeck internal to
  slice 2's schema builder, which is exactly the leak the design's strongest rule forbids. Confirm
  or overturn while implementing the schema builder, and write it down either way.

  ***Settled 2026-08-11, implementing slice 2.***
  - **`reliable=False` refuses at `build()`**, rather than reaching a runtime check. For a *tool*
    there is nothing to defer to: the model-visible schema has to exist at build time, so an
    unreadable signature has no honest one to offer, and deferring would mean emitting a schema
    that is fiction. Refusal is also strictly non-regressive — #172 rejected every bare callable
    today, so nothing that works now stops working. The invocation-time net still exists, for the
    case that genuinely can only be caught late: a compiled tool played by a run carrying
    something other than a `RunContext` raises naming the callable instead of calling it with an
    argument missing.
  - **Bare `Context` confirmed as `Context[Any]`**, with the schema builder now in hand: it
    injects, and the parameter is absent from `params_json_schema` exactly as a parameterised one
    is. A test pins both halves.
- **Slice 3.** The contract test parametrized over both engines is a **done-when, not a nice to
  have**: the plan names it as "the only thing keeping them honest", and two bridges producing one
  advertised behavior is the top risk in the whole feature. Hooks belong here, not in slice 2 —
  the design plan mentions them in both step 4 and step 6, and doing them twice is the default
  failure if nobody says which.

  **`resume()` loses the context, silently — slice 3 must close it.** Found by slice 2 and
  verified in the tree: `Runtime.run` passes `data=context` (`runtime/service.py:146`), while
  `resume_run` (`:231`) and the workflow resume path (`:200`) mint their `RunContext` with no
  `data=` at all, so it defaults to `None`. A run paused and resumed therefore replays with its
  context gone. The design plan already rules the right behavior — *"`resume()` resupplies it"*
  (Lifecycle) — so this is unimplemented, not undecided.

  It is the worst-shaped bug in the arc: **undetectable from inside**, because context is never
  serialized, so nothing in the log can be compared against what should have been there. A tool
  reading `ctx.data.client` gets an `AttributeError` on `None` at best; one written defensively
  as `if ctx.data:` degrades in silence and returns a plausible wrong answer.

  Not shippable-broken today only because PR #210 stays draft until slice 4 — `run(context=)`
  turned on in slice 2, so the path is already reachable on the branch. Do not let this arc reach
  `dev` with it open.

  ***Settled 2026-08-11, implementing slice 3.***
  - **A node whose signature cannot be read is left alone, not refused** — the opposite of slice
    2's ruling for a *tool*, and for the reason that ruling gave: a tool must publish a
    model-visible schema at build time, so an unreadable signature has no honest one to offer. A
    node publishes no schema, so running it exactly as langgraph would have is a change to
    nothing. Refusing would also be a regression here, unlike for tools: #172 already rejected
    every bare callable in `tools=`, while a graph full of decorated nodes works today.
  - **A node is bridged by rewriting the graph, in `authoring/graphs.py`, at `build()`.** langgraph
    injects by parameter *name* (`runtime`, `config`, `writer`, `store`), so a `Context[...]`
    annotation under any name reaches a node only if something puts it there — and the author
    builds the `StateGraph` themselves, so the graph AgentDeck is handed is the only seam. Only a
    node langgraph itself wrapped as a plain callable is touched; any other `Runnable` is
    engine-native and untouched. The one internal reach is reading the callable back off
    `RunnableCallable.func`/`.afunc`, which is the price of not owning `add_node`.
  - **The bridge forwards every other langgraph-injected parameter**, so a node declaring
    `config: RunnableConfig` next to its context still reaches the reporter where Ruling B left
    it. Nothing about `configurable` moved.
  - **Hooks are bridged per method, and the context must be the hook's first parameter.** The SDK
    calls hooks positionally with its wrapper first; a substitution anywhere else could only be a
    guess about which remaining argument was meant. A hooks object declaring no `Context[...]` is
    returned as it was given — engine-native, zero regression.
  - **A callable in `instructions=` composes its MCP banner and skills disclosure at call time**
    rather than at compile time. `refresh_mcp_status`'s prefix surgery measures a string, and a
    closure has no prefix to measure; composing per turn makes that pass a no-op for callables
    instead of a source of stale banners.
  - **The headless `Workflow.run()`/`as_tool()` paths are deliberately not bridged.** Neither has
    a `RunContext` to unwrap, and the bridge is wired at `InvocableRegistry.load()` — the path
    every `Deck` run takes. Verified rather than assumed: a `Context`-declaring node run that way
    dies with langgraph's own `TypeError: book() missing 1 required positional argument`, *not*
    with the bridge's safety net, because the safety net lives inside the bridge that path never
    installs. Loud and immediate, but less legible than the compiled path's message — which is
    the cost of leaving a log-free dev convenience alone.
- **Slice 4.** Must rewrite `docs-site/content/reference/deck.mdx`, which currently ends on
  "There is no `context=`" as a deliberate promise. The `test_every_public_deck_method_is_documented_somewhere`
  guard in `tests/test_docs_site.py` will notice the changed signatures.

  ***Settled 2026-08-11, implementing slice 4.***
  - **Ruling 10's invocation-time half is deliberately not implemented.** `build()` checks the
    declared type against every requirement; nothing checks at `run()` that a root whose graph
    requires a context actually received one, and nothing `isinstance`-checks the value against
    the declaration. Not an omission — the two would contradict what this arc has already
    settled and shipped. Ruling A's documented behavior for the unsupported combination *is* a
    replay with `data=None` ("surfaces wherever the node first touches it"), slice 3 shipped and
    documented the same for a `resume()`/`answer()` lifted without one, and
    `test_a_run_without_a_context_reaches_a_declaring_tool_with_none` pins it for `run()`. A
    pre-execution refusal would turn all three into errors. It stays additive and reversible: if
    a real caller wants "required", it is a later, opt-in decision, not one to take days before
    a stable tag.
  - **The declaration is checked only when made, and only when it is a type.** No `context=` on
    the constructor means no build-time check at all, so every deck built against slices 1–3 is
    unaffected. An *instance* (`Deck(context=my_calendar)`) or a union is refused at
    construction rather than accepted: both make every check below defer, which is the
    accepted-and-useless shape #182 deleted the parameter for.
  - **Step 8 has no code subject, and the plan is wrong about it.** The design plan's step 8
    ("Skills — in-process `Context[T]`") predates #71/#164: a v3 skill is a `SKILL.md` an agent
    reads through the generated `load_skill` tool, and the executable half — `skill_runtime`, the
    sandbox scaffolding — was deleted. There is no user callable at a skill to inject into, so
    the honest implementation is a sentence in the reference saying a skill never receives a
    context and that a skill needing application state is an ordinary tool. Nothing was built.
  - **Four sites flattened the error class, and had to be fixed with it.** `compile.py`,
    `graphs.py`, `hooks.py` and `discovery.py` each re-raise a compilation failure with a name
    prepended (`agent 'X': …`, `node 'y': …`, `<file> failed to build: …`), all as a bare
    `ConfigError`. Left alone, `ContextTypeError` would have been unobservable from outside the
    unit tests — the class the plan names as the user-visible type, erased by the wrap. Each now
    re-raises the original's class.
  - **Union annotations are the one real trap in the compatibility check.** `get_origin(A | B)`
    is `types.UnionType` on 3.13 and `typing.Union` on 3.14, and *both are classes* — so a check
    that normalises through `get_origin` and falls through to `issubclass` compares the declared
    type against `UnionType` itself and refuses every union. `Context[Calendar | None]` already
    appears in slice 2's own tests. Handled arm by arm, with a named regression test.

## Rulings

Two questions the design plan could not have answered, because the API it describes did not exist
yet. **B is settled; A is open.** Neither blocks slices 1–2.

### A. Does `answer()` take a context? And what does `tick()` do without one? — **RULED**

`Deck.answer(run_id, value)`, `tick()` and `due_resumes()` became public API in #164, after the
plan was written. It covers `run()` and `resume()` ("`resume()` resupplies it", Lifecycle) and is
silent on all three.

**`answer()` is the easy half:** an optional `context=` mirroring `run()`, mandatory under Ruling
10 when the graph requires one. Same shape, no new concept, and a human answering an interrupt is
present to supply it.

**`tick()` is the hard half, and it is still live.** Removing the durable-timer feature was
considered on 2026-08-11 and **rejected** — v3.0.0 ships `sleep_until`/`tick()`/`due_resumes()` as
they are, because pulling a working capability before a stable tag costs users more than its
underlying defect costs us (that defect is #212: two paused-run inboxes disagreeing because
`AGENTDECK_EVENTS` defaults to `memory://` while `AGENTDECK_CHECKPOINT` defaults to `sqlite://`).

So the problem stands. `tick()` is an autonomous resume with nobody present to supply anything, and
under Ruling 10 —

> Every root whose graph requires context must receive a compatible instance, checked **before
> execution**.

— a durable, context-requiring workflow that sleeps is **un-resumable by `tick()`**. Context is
never serialized into the event log (Lifecycle), so it cannot be recovered from the checkpoint
either. The options:

1. **`Deck(context_provider=...)`** — a zero-arg callable the deck holds, used by `tick()`. Fits
   the ownership rule the Deck already has, and makes the autonomous path a first-class case
   rather than an exception.
2. **`tick()` skips such a thread and emits an event saying why.** A *loud* skip is not the Wave B
   failure class — a silent one would be. Cheap and honest, but a documented combination (durable
   + timer + context) then does nothing.
3. **`tick(context=...)`** — pushes the problem to the cron job, which has no idea which workflows
   are due or what each needs.

**RULED 2026-08-11: ship the limitation, documented. None of the three.**

`tick()` gains nothing, `Deck` gains no `context_provider`, and the combination of *durable +
`sleep_until` + `Context[T]`* is simply not supported in v3.0.0 — stated in the reference rather
than worked around. Slice 3 already wrote it into `docs-site/content/reference/deck.mdx`.

The reasoning: option 1 adds public API for a combination nobody has asked for, days before a
stable tag that freezes it — and a `context_provider` is a second, quieter way to supply context
alongside `run(context=)`, on a milestone named *one way to work*. Option 2 spends design effort
on a skip path for the same unrequested combination. The honest move is to say what does not work
and leave the surface clean; adding a provider later is additive, and by then there may be a real
caller to shape it around.

**What slice 4 owes this ruling** is not code but precision. The reference must say which
combination is unsupported and what happens if you build it — not a vague caveat. Today a
`tick()` resume of a context-requiring workflow replays with `data=None`, which surfaces wherever
the node first touches it, so the docs should name that rather than let it be discovered.

*Delivered 2026-08-11:* `reference/deck.mdx` gained a "Where a context does not reach" section
naming the triple (`durable = True` + `sleep_until` + `Context[T]`), the mechanism (`ctx.data` is
`None` on the replay), and both ways it shows up — an `AttributeError` on `None`, or a plausible
wrong answer from a node written as `if ctx.data:`. No workaround is offered, because there is
none.

Revisit if a real user hits it: the provider is additive and this ruling costs nothing to reverse.

The wider form of this outlives whichever option wins, and slice 4 must state it: **a context
cannot cross the HTTP surface at all**, because it is a live Python object the plan says is never
serialized. A context-requiring invocable is therefore reachable from embedded Python callers and
not from `asgi()`. That is a real boundary, and the reference has to say so rather than let a user
find it in production. *Stated in the same section, alongside the two headless paths that pass no
context either (`Agent.run()`, `Workflow.run()`/`as_tool()`) and the skills non-case.*

### B. Does `reporter` move off `configurable`? — **RULED 2026-08-11: no**

Per §3. **Smallest change, or none.** Application context moves to `Runtime[T]`; `reporter` and
`agentdeck_stream` stay on `configurable` exactly as they are. The plan's "`thread_id` — and
nothing else" is amended to "nothing else *application-owned*", which is the distinction that
actually matters; no code moves.

If `Context[T]` lands, `ctx.reporter` arrives with it additively at no extra cost — that is what
"v3 supports the ability" means here, and it settles nothing beyond that.

The larger question — whether reach is an explicit parameter or an ambient accessor, whether
LangGraph's transport should be `Runtime[T]` rather than a config channel, and whether a
deck-level sink parameter belongs — is **#211**, deliberately deferred past the v3.0.0 release.
Slice 3 must not pre-empt it: leave the key, and do not deprecate it in passing.

## Risks

- **Two bridges, one contract** — the plan's own top risk, and unchanged. Mitigated only by the
  slice-3 contract test.
- **The compiler is where scope creep arrives.** Permissions, approvals, retries and telemetry all
  have a natural home there. None are v3; say so in the module docstring so the next contributor
  finds the fence already built.
- **Slice 2 touches the error message three shipped documents quote.** Grep `docs-site/` and
  `docs/` for the guardrail text before changing it, not after.
