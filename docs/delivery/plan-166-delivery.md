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
- **Slice 3.** The contract test parametrized over both engines is a **done-when, not a nice to
  have**: the plan names it as "the only thing keeping them honest", and two bridges producing one
  advertised behavior is the top risk in the whole feature. Hooks belong here, not in slice 2 —
  the design plan mentions them in both step 4 and step 6, and doing them twice is the default
  failure if nobody says which.
- **Slice 4.** Must rewrite `docs-site/content/reference/deck.mdx`, which currently ends on
  "There is no `context=`" as a deliberate promise. The `test_every_public_deck_method_is_documented_somewhere`
  guard in `tests/test_docs_site.py` will notice the changed signatures.

## Rulings

Two questions the design plan could not have answered, because the API it describes did not exist
yet. **B is settled; A is open.** Neither blocks slices 1–2.

### A. Does `answer()` take a context? And what does `tick()` do without one? — **OPEN**

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

I would take (1). It is additive and the only option where the combination works. It is needed by
**slice 4**, not before.

The wider form of this outlives whichever option wins, and slice 4 must state it: **a context
cannot cross the HTTP surface at all**, because it is a live Python object the plan says is never
serialized. A context-requiring invocable is therefore reachable from embedded Python callers and
not from `asgi()`. That is a real boundary, and the reference has to say so rather than let a user
find it in production.

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
