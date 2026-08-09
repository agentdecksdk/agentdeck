# Plan — phase 4: `Deck`, the v3 composition API

**Status:** proposed · **Date:** 2026-08-09 · **Target: v3.0.0 (breaking)**
Supersedes the Option A recommendation in `decision-v3-entry-point.md` where they differ.
Resolves #88. Blocks phases 5–6.

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | How is an agent declared? | **`Agent(...)` constructor, which may take a declaration as its base and override on construction.** |
| 2 | Deck-level `mcp=`, given the process-wide registry | **`mcp.json` is the single source of truth at deck level. A deck per tenant is a *process* per tenant** — so the global registry is acceptable and is not rework for this phase. |
| 3 | `agents=` / `workflows=` or one `invocables=` | **Separate.** Both are executable roots reachable by `deck.run`, *and* an agent may hold a workflow as an ability. |
| 4 | An explicit `build()` | **Yes** — idempotent, validates only, opens nothing. `async with` calls it if you did not. |
| 5 | Deck declares the context type | **Yes** — `Deck(context=MiddleContext)`; see `plan-context-injection.md`. |

## Target shape

```python
booking_agent = Agent(
    name="booking",
    instructions=booking_instructions,     # str or a Context-taking callable
    tools=[find_slots, book_slot],
    skills=["booking", "rescheduling"],    # names, resolved from the deck's skill roots
    mcp=["calendar", "crm"],               # names, resolved from the deck's mcp.json
)

deck = Deck(
    agents=[booking_agent, support_agent],
    workflows=[onboarding_workflow],
    skills=["./skills", "./company-skills"],
    mcp="mcp.json",
    context=MiddleContext,
)
deck.build()

async with deck:
    result = await deck.run(
        "booking", message,
        session_id=conversation_id,
        namespace=f"business:{business_id}",
        context=MiddleContext(business=..., customer=..., calendar=...),
    )
```

Two constructors, one primitive:

```python
Deck(agents=..., workflows=..., skills=..., mcp=..., context=...)   # code-first
Deck.from_project("./.agentdeck")                                   # today's directory, unchanged
```

`from_project` fills `agents`/`workflows`/`skills`/`mcp` by discovery and is **sugar over the
same constructor** — one catalog mechanism underneath, which is #88's own rule.

## The ownership rule

*The deck closes what it constructed and never closes what you passed in.* That is what makes
deck-per-process safe without a resource-manager abstraction, and it is unchanged from #88.

## What `Deck` does NOT take

`engines=`, `tools=`, `runtime=`, `store=`… are **not** normal composition. Engine and runtime
selection is infrastructure behind the abstraction, resolved from settings. `engines=` survives
as a **test-only** keyword because `tests/contract/` needs the stub engine; it is not documented
in the public reference.

## Phases

### 4a — `authoring/`
`Agent` and `Workflow` constructors, plus the declaration base they can be built from.
`BaseAgent`/`BaseWorkflow` move here from `agents/`/`workflows/` and become the *declaration*
form; `Agent(base=SomeDeclaration, instructions=...)` overrides on construction. Node classes
(`SkillNode`, `LoadFileNode`, `AgentNode`) move to `authoring/nodes.py`. Everything compiles to
`InvocableSpec`, so the Runtime is untouched.

**Open:** the exact override mechanics of `Agent(base=...)` — positional base, `base=` keyword,
or `SomeDeclaration.with(...)`. Not decided; a ruling is needed before 4a is written.

### 4b — skill roots and the registry
`skills=` takes a path or paths. Discovery walks each root for `*/SKILL.md` and builds one
registry keyed by directory name. `Agent(skills=["booking"])` resolves against it. Users never
construct a `Skill(...)`. Duplicate names across roots are a `build()` error naming both paths —
one name is one skill, the same rule `PluginRegistry` already applies to bundles.

### 4c — MCP from one file
`mcp="mcp.json"` becomes the single source of truth, replacing the `mcp:` section of
`config.yaml` and `AGENTDECK_MCP_SERVERS`. `Agent(mcp=["calendar"])` resolves names against it;
an unknown name is a `build()` error rather than the current silent drop.

**Note:** this reverses #78, which moved MCP config *out* of `.mcp.json` into `config.yaml`. The
reversal is deliberate (one file, Claude-Code-shaped, per-deck) and interacts with **#155** — the
env-surface restructure has to account for `mcp:` leaving `config.yaml`.

### 4d — `Deck` itself
The class, both constructors, `build()`, and the lifecycle. Roughly `app.py`'s composition-root
half moved almost verbatim, plus the event-stream reducers (`_turn_result`, `_workflow_result`)
unchanged.

**Surface:**
```python
await deck.run(name, input, *, session_id=None, namespace=None, run_id=None, context=None)
deck.stream(...)                       # same options, yields events
await deck.pause(run_id)  /  cancel(run_id)  /  status(run_id)
await deck.resume(run_id, *, context=None)
await deck.pending(namespace=None)
deck.build()  ·  async with deck  ·  deck.asgi()
properties: runtime · store · agents · workflows · skills · settings
```

`pause`/`cancel`/`status` are new names over `Runtime.signal` and the store's status projection —
flat verbs, per your spec.

### 4e — serve, unchanged behavior
`agentdeck serve` keeps serving `./.agentdeck` exactly as today: it becomes
`Deck.from_project()` + `deck.asgi()`. The HTTP contract and `tests/golden/` do not move — this
phase is a Python-API change only, and the wire staying byte-identical is what proves it.

### 4f — deletion
`agents/` and `workflows/` deleted, `app.py` reduced to the composition root or removed
entirely. No re-export facades: v1's Python API is dropped, per the cutover ruling.

## What `build()` checks

- every agent/workflow compiles to an `InvocableSpec`
- every `Agent(skills=[...])` name resolves in the skill registry
- every `Agent(mcp=[...])` name resolves in `mcp.json`
- the engine each spec needs is registered
- **context compatibility across the whole graph** — `plan-context-injection.md`

It opens no connections, starts no MCP server, and touches no network. That is what makes it
usable as `agentdeck check` in CI.

## Deliberately deferred

- **Splitting `Execution` out of `RunContext`** (gate/reporter/user-context beside a three-field
  `RunContext`). Ruled *yes, but later* — it re-threads every port and is not needed for this
  phase to ship.
- **Instance-scoping `MCPLifecycle`.** Unnecessary while a deck is a process.
- **`tools=` at deck level.** Agents own their tools; there is no second place to put them.

## Risks

- **The authoring change is the migration.** Every `.agentdeck/agents/*/agent.py` in existence
  changes shape. The golden fixture project is rewritten, which means `tests/golden/` fixtures
  move even though the wire does not — those two must not be confused during review.
- **4c reverses a shipped decision (#78).** If #155 lands first the two conflict; sequence them.
- **`status(run_id)` has no cheap implementation today.** `EventStorePort.run_status` folds a
  run's events; a deck-level `status` over many runs may want the store-side projection #45's
  follow-up describes.
