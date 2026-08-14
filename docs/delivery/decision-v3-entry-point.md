# Decision brief — the v3 entry point

**Date:** 2026-08-08 · **Branch:** `feat/v3-cutover` · **Resolves:** #88 · **Status:** closed — both
deviations signed off, `Deck` shipped in #164, and this was the v3.0.0 public API at the freeze.

**Ship `Deck`: one class, two constructors, flat methods, one ownership rule** — #88's shape with
`serve()`, `tools=` and `engines=` dropped, and defaults resolved from settings rather than
hard-coded. ~220 lines, most of it lifted out of `app.py`.

```python
async with Deck.from_project() as deck:            # today's ./.agentdeck, unchanged
    turn = await deck.chat("Greeter", "sess-1", "hello")
```

`Runtime` cannot own lifecycle for things it did not construct (MCP servers, the Redis session
client) without importing an adapter, which is why a lifecycle owner above it is required rather
than convenient. A third of `App` is already a `Runtime` pass-through.

## What changed under #88 while it sat open

#88 was written on 2026-08-05, before v2.0.0 shipped and before phases 0–3 of this branch.

| # | #88 assumed | Reality on 2026-08-08 |
|---|---|---|
| 1 | #74 would be re-scoped to build this shape first | #74 is shipped; `App` *is* the composition root and the facade it built is an **HTTP** one, which survives v3 untouched |
| 2 | `App` becomes a thin wrapper over `AgentDeck.from_project()` | Superseded by `plan-v2-cutover.md` ruling 1: v1's public API is dropped, not facaded — no alias, no shim |
| 3 | `engines=None` is *inferred* | Engines are no longer default-constructible, so "inferred" means the deck *constructs* the two defaults from settings; `engines=` stays an override for `tests/contract/` |
| 4 | Defaults are ephemeral, single-process, no sinks | Defaults are settings-resolved: `LangfuseSink` when keys are present, store and control through `resolve_*()` |
| 5 | A `durable=True` workflow on a memory deck is refused at construction | The corollary dissolves — `durable` is spec metadata and the checkpointer is a different backend from the event store; coherence is #155's territory |
| 6 | `tools=` cut to deck-level MCP sources | `MCPLifecycle` is a process-wide class-level registry, so per-deck tool sources are not expressible at all; stays cut |
| — | (unmentioned) the ADR-D5 second store | The deck constructs and closes `SessionFactory`/`ExecutionStore` too; `session_factory=` stays the DI seam |
| — | (unmentioned) the sandbox port | Resolved at the composition root and passed into engines, not a constructor argument — one implementation |

| Cross-check | Verdict |
|---|---|
| #129 protocol surfaces | `asgi()` stays singular; a protocol list is reconsidered there, on evidence |
| #120 two approval inboxes | Collapses in v3's favour — deleting `App` deletes the checkpointer-reading side |
| #26 stub-runner harness | Becomes "package the fixtures", not "invent a seam" |
| #155 env surface | Changes *which* variables resolve defaults, not the rule that arguments win |
| #131 / #71 / #132 | All want the entry point smaller; every option was judged against that |
| #119 bare exceptions from bundle builds | Applies to `Deck.from_project()` unchanged, same gap |

## Migration — every `App` member gets a ruling

| `App` member | v3 | Note |
| --- | --- | --- |
| `App()` / `App.open()` | `async with Deck.from_project()` | Same discovery, same failure mode; the deck *is* the context manager. |
| `load()` | folded into construction | `inventory` becomes `Deck.invocables`. |
| `runtime` / `store` / `settings` | kept, same names | |
| `run_agent(name, msg)` | `deck.run(name, msg)` → `TurnResult` | One-shot, no session. |
| `chat(name, sid, msg)` | `deck.chat(name, sid, msg)` | Unchanged signature. |
| `chat_stream(name, sid, msg)` | `deck.stream(name, msg, session=sid)` | Renamed because it streams workflows too. |
| `run_workflow(name, state, thread_id=)` | `deck.run(name, state, thread=…)` | One front door; `InvocableSpec` already erases the distinction. |
| `resume_workflow(name, thread, value)` | `deck.resume(name, thread, value)` | Same "not applied → `NotFoundError`" guard. |
| `run_workflow_stream(...)` | **dropped** | The one method that bypasses the Runtime: no event log, invisible to `resume`. Frame shapes differ. |
| `pending_interrupts(name=None)` | `deck.pending(invocable=None)` | Reads the event log, not the checkpointer — closes #120, CHANGELOG-declared. |
| `due_resumes(now=)` | **dropped** | Filter `deck.pending()` on `wake_at_of(p.payload)`. |
| `tick(now=)` | **dropped** | A for-loop over `deck.resume(...)`; AgentDeck runs no daemon. |
| `session_for(sid)` | `deck.session_for(sid)` | The ADR-D5 store's only public door. |
| `pause_run` / `cancel_run` | `deck.signal(run_id, Signal.PAUSE\|CANCEL, reason)` | The verb is already an enum. |
| `resume_run(run_id)` | `deck.resume_run(run_id)` | Distinct from `resume` — continues a *paused* run. |
| `agents` / `workflows` / `skills` registries | **dropped** | They expose `BaseAgent` classes; `deck.invocables` replaces them. |
| `TurnResult` | kept, unchanged | |

`test_app.py` (17 KB) and the 7 gate-executed ` ```python run ` fences in `docs-site/content/`
must be rewritten against `Deck` **before** `app.py` is removed, or coverage drops silently.

## Rulings

| # | Ruling |
|---|---|
| 1 | `App` does not survive — deleted outright, no alias, no deprecation shim, the migration table above is the compatibility story. |
| 2 | Flat methods, not handles; shipping both is two ways to do one thing at the freeze point. |
| 3 | No `serve()` at all — `agentdeck-serve` is the process entry point and embedders write `uvicorn.run(deck.asgi())`. |
| 4 | `asgi()` registers no lifespan hook, because Starlette does not run a mounted sub-app's lifespan and it would fire standalone but not mounted. |
| 5 | The name is `Deck`; `agentdeck.AgentDeck` stutters. |
| 6 | One `invocables=`, not `agents=`/`workflows=`, so the front door does not reinstate the split `InvocableSpec` removed. |
| 7 | The deck owns the ADR-D5 session store, because it constructs it; `session_factory=` stays the DI seam. |
| 8 | `tools=` stays cut with a documented reason until `MCPLifecycle` is instance-scoped. |
| 9 | Defaults resolve from settings and an argument always wins. |

Rulings 1–5 close #88's five open decisions; 6–9 close the four that phases 0–3 created. Rulings 3
and 5 are the two places this brief deviated from #88's written sketch, and both were signed off.

Options B (`Runtime` as the entry point, ~60 lines) and C (#88's full sketch, ~450 lines plus an MCP
refactor) were rejected: B externalizes the terminal-event reducer onto every user, C freezes five
extra decisions nothing pressures and needs a `tools=` blocker resolved first.

Deliberately left open: who builds `InvocableSpec.native`, where coding standards §3 and the
registry's placement "at the composition layer's edge" (design doc §6, amended 2026-08-05) pull
against each other — an `authoring/` question phase 4 must carry, not an entry-point one; whether
the two copies of the terminal-event reducer become one shared module; and the #155 ordering.

The phase-4 PR owed CLAUDE.md a dated amendment to its single-entry-point rule, with the
primitive/sugar split as the reason there is still exactly one catalog mechanism underneath.

## Divergences from what shipped

| Item | Divergence |
|---|---|
| Ruling 6 | Reversed by `plan-phase4-deck.md` (2026-08-09); the shipped constructor is `Deck(agents=…, workflows=…, skills=…, mcp=…)` over one catalog. |
| Ruling 2's method list | `chat`, `signal` and `resume_run` do not exist on today's `Deck`: `pause`/`cancel` are separate verbs, `resume` continues a paused run and `answer` supplies an interrupt value. |
| Ruling 2's flat methods | Amended 2026-08-14 by `design/run-operations.md`: the eight run-scoped verbs group under `deck.runs.*`. Not the handle this ruling rejected — the run id stays an argument and there is still one way to address a run. |
| `tick` / `due_resumes` | Dropped here, shipped on `Deck` anyway, and removed again by `design/run-operations.md` (2026-08-14) — which keeps the "a for-loop is not an API" half of the reasoning and amends the other half: a sweep scoped to the deck's own lifetime now runs internally, so the capability survives without the user wiring a cron. |
| `pending` | Takes `namespace=`, not `invocable=`. |
| `runtime` / `store` | Not exposed; `review-phase4-deck.md` ruled them API leaks. |
