# Delivery plan  -  #166 `Context[T]` injection

**Date:** 2026-08-11 · **Baseline:** `dev` at `659f64d` · **Status:** delivered, four slices,
all rulings taken. Delivery companion to `docs/delivery/plan-context-injection.md`, which holds the
design and its ten rulings, is dated 2026-08-09, and predates #164, #182, #172, #203, #205 and #155. #166 is the last
large feature in v3 (`roadmap-v3.md`, Ruling 3).

## What moved under the design plan

| # | What the design plan said | What the tree said |
|---|---|---|
| 1 | Keep `Runner.run_streamed(..., context=ctx)` | Already the code, deliberately (`openai_agents/engine.py:185`); step 4's real work is the bridge from a user callable's `Context[T]` to `wrapper.context`. |
| 2 | Add `RunContext.data` | `core/context.py:32-36` carries a standing rule against fields AgentDeck's own machinery never reads; `data` passes it, but slice 1 amends the paragraph in the same commit and states that `data` is application-*owned*, not application-*identity*. |
| 3 | `configurable` keeps "`thread_id`  -  and nothing else" | It carries `thread_id`, `agentdeck_stream` and `reporter`, and `REPORTER_KEY`'s docstring defends the last as langgraph's own injection channel  -  moving it is a behaviour change against a documented decision. Ruling B. |
| 4 | Plain callables are the canonical declaration | #172 shipped a `build()` rejection of bare callables (`authoring/compile.py:110-115`); a `Context[T]` callable cannot be pre-decorated, so slice 2 owns turning that rejection into compilation, along with the error text #179's three documents quote. |

## Slices

Four PRs in sequence, under one rule: **a public knob turns on only in the slice where it works**  -
#182 deleted `Deck(context=...)` because accepted-then-refused is a false promise.

| PR | Steps | Ships | Public surface turned on |
|---|---|---|---|
| **1** | 1–2 | `Context[T]`, `RunContext.data`, callable analysis | **none**  -  internals only |
| **2** | 3–4 | the compiler + the OpenAI bridge, end to end on one engine | `run(context=)`, `Context` exported |
| **3** | 5–6 | LangGraph bridge; dynamic instructions and hooks | LangGraph parity |
| **4** | 7–8 | `Deck(context=T)`, build-time graph validation, `ContextTypeError`, skills, docs | `Deck(context=)` |

Steps 3 and 4 are not split: a compiler abstraction with no engine consuming it is an interface with
one implementation. Callable analysis is stdlib introspection, whose edge cases are
`from __future__ import annotations`, `functools.wraps`, a decorator that destroys the signature
(report *unreliable*, never guess), zero/one/two `Context[...]` parameters, and method vs function.
Slice 3's contract test parametrized over both engines is a done-when, not a nice to have.

## Rulings settled while implementing

| Date | Slice | Ruling |
|---|---|---|
| 2026-08-11 | 2 | `reliable=False` refuses at `build()` rather than deferring: a tool's model-visible schema must exist at build time, so an unreadable signature has no honest one to offer, and refusal is non-regressive because #172 already rejected every bare callable. |
| 2026-08-11 | 2 | The invocation-time net survives for the case only catchable late  -  a compiled tool played by a run carrying something other than a `RunContext` raises naming the callable. |
| 2026-08-11 | 2 | Bare `Context` is `Context[Any]`; it injects, and the parameter is absent from `params_json_schema` exactly as a parameterised one is. Leaving it unparameterised would hand an AgentDeck internal to the schema builder. |
| 2026-08-11 | 3 | A node whose signature cannot be read is left alone, not refused  -  the opposite of slice 2's ruling for a tool, because a node publishes no schema and refusing would regress graphs of decorated nodes that work today. |
| 2026-08-11 | 3 | A node is bridged by rewriting the graph in `authoring/graphs.py` at `build()`, since langgraph injects by parameter *name* and the author's own `StateGraph` is the only seam; only a plain callable langgraph wrapped is touched, read back off `RunnableCallable.func`/`.afunc`. |
| 2026-08-11 | 3 | The bridge forwards every other langgraph-injected parameter, so a node declaring `config: RunnableConfig` still reaches the reporter where Ruling B left it. |
| 2026-08-11 | 3 | Hooks are bridged per method with the context as the hook's first parameter, because the SDK calls hooks positionally with its wrapper first; a hooks object declaring no `Context[...]` is returned unchanged. |
| 2026-08-11 | 3 | A callable in `instructions=` composes its MCP banner and skills disclosure at call time, since `refresh_mcp_status`'s prefix surgery measures a string and a closure has no prefix to measure. |
| 2026-08-11 | 3 | The headless `Workflow.run()`/`as_tool()` paths are deliberately not bridged  -  neither has a `RunContext`, and the bridge is wired at `InvocableRegistry.load()`; verified to die with langgraph's own `TypeError` rather than the bridge's safety net. |
| 2026-08-11 | 3 | **`resume()` losing its context is a bug, not a decision**  -  `Runtime.run` passes `data=context` (`runtime/service.py:146`) while `resume_run` (`:231`) and the workflow resume path (`:200`) mint a `RunContext` with no `data=`, so a resumed run replays with its context gone, undetectably because context is never serialized. Reachable on the branch from slice 2, unshipped only because PR #210 stayed draft; closed in slice 3. |
| 2026-08-11 | 4 | Ruling 10's invocation-time half is deliberately not implemented: a replay with `data=None` is the behaviour Ruling A documents and slice 3 shipped, so a pre-execution refusal would turn three settled cases into errors. Additive and reversible. |
| 2026-08-11 | 4 | The declaration is checked only when made and only when it is a type; an instance or a union in `Deck(context=…)` is refused at construction rather than accepted-and-useless. |
| 2026-08-11 | 4 | Step 8 has no code subject and the design plan is wrong about it, because it predates #71/#164  -  a v3 skill is a `SKILL.md` read through the generated `load_skill` tool and the executable half was deleted, so the honest delivery is a reference sentence saying a skill never receives a context. |
| 2026-08-11 | 4 | `compile.py`, `graphs.py`, `hooks.py` and `discovery.py` each re-raised a compilation failure as a bare `ConfigError`, which erased `ContextTypeError`; each now re-raises the original's class. |
| 2026-08-11 | 4 | `get_origin(A \| B)` is a class on both 3.13 and 3.14, so a check normalising through `get_origin` refuses every union; unions are handled arm by arm, with a named regression test. |

### A. Does `answer()` take a context? And what does `tick()` do without one?  -  RULED 2026-08-11

`answer()` gains an optional `context=` mirroring `run()`. `tick()` gains nothing, `Deck` gains no
`context_provider`, and **durable + `sleep_until` + `Context[T]` is simply unsupported in v3.0.0**  -
`tick()` is an autonomous resume with nobody present to supply a context, and context is never
serialized, so it cannot be recovered from the checkpoint either. Adding a provider later is
additive; adding a second, quieter way to supply context days before a stable tag is not.

Removing the durable-timer feature was considered on 2026-08-11 and rejected: v3.0.0 ships
`sleep_until`/`tick()`/`due_resumes()` as they are, because pulling a working capability costs users
more than #212 costs us.

*Delivered 2026-08-11:* `reference/deck.mdx` gained a "Where a context does not reach" section
naming the triple, the mechanism (`ctx.data` is `None` on the replay), both ways it shows up (an
`AttributeError` on `None`, or a plausible wrong answer from a node written as `if ctx.data:`), the
fact that **a context cannot cross the HTTP surface at all**, the two headless paths, and the skills
non-case. No workaround is offered, because there is none. Revisit if a real user hits it.

### B. Does `reporter` move off `configurable`?  -  RULED 2026-08-11: no

`reporter` and `agentdeck_stream` stay exactly as they are; the design plan's "`thread_id`  -  and
nothing else" is amended to "nothing else *application-owned*", and no code moves. `ctx.reporter`
arrives additively with `Context[T]`. The wider question  -  explicit reach vs ambient accessor,
`Runtime[T]` vs a config channel, a deck-level sink parameter  -  is #211, deferred past v3.0.0, and
slice 3 must not pre-empt it by deprecating the key in passing.

## Risks

- **Two bridges, one contract**  -  the design plan's top risk, mitigated only by slice 3's contract test.
- **The compiler is where scope creep arrives.** Permissions, approvals, retries and telemetry all
  have a natural home there and none are v3; the fence is stated in the module docstring.
