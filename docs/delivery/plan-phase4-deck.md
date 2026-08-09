# Plan — phase 4: `Deck`, the v3 composition API

**Status:** proposed · **Date:** 2026-08-09 · **Target: v3.0.0 (breaking)**
Supersedes the Option A recommendation in `decision-v3-entry-point.md` where they differ.
Resolves #88. Blocks phases 5–6.

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | How is an agent declared? | **`Agent(...)` constructor, which may take a declaration as its base and override on construction.** |
| 2 | Deck-level `mcp=`, given the process-wide registry | **One MCP file is the single source of truth at deck level. A deck per tenant is a *process* per tenant** — so the global registry is acceptable and is not rework for this phase. |
| 3 | `agents=` / `workflows=` or one `invocables=` | **Separate.** Both are executable roots reachable by `deck.run`, *and* an agent may hold a workflow as an ability. |
| 4 | An explicit `build()` | **Yes** — idempotent, validates only, opens nothing. `async with` calls it if you did not. |
| 5 | Deck declares the context type | **Yes** — `Deck(context=MiddleContext)`; see `plan-context-injection.md`. |
| 6 | Root name identity | **One global root namespace.** `deck.run(name)` addresses either collection, so an agent and a workflow sharing a name is a `build()` error naming both. |
| 7 | A workflow as an agent's ability | **Everything in `workflows=` is registered and root-invocable.** An agent references a *registered* workflow by name or object; an agent can never introduce a workflow the catalog does not already hold. |
| 8 | Lifecycle | **`NEW → BUILT → OPEN → CLOSED`.** The catalog is immutable after `BUILT`, or the validation guarantee is worthless. `run`/`stream` require `OPEN`; `asgi()` owns OPEN/CLOSED through the ASGI lifespan. |
| 9 | `Agent(base=…)` mechanics | **`base=` keyword only.** No positional base, no `.with(...)`. `Agent(...)` stays the one construction API. |
| 11 | Sandboxing | **A future ability, currently disabled and out of scope (#163).** No agent, tool or skill is sandboxed in v3; `BaseSandboxAgent` goes with `agents/` and nothing replaces it in `authoring/`. |
| 10 | Declaration naming | **`BaseAgent` → `AgentDeclaration`, `BaseWorkflow` → `WorkflowDeclaration`.** In a breaking release, calling something `Base*` when it is a declarative *input* to `Agent(...)` is conceptual debt with no upgrade path later. |

## Target shape

```python
booking_agent = Agent(
    name="booking",
    instructions=booking_instructions,     # str or a Context-taking callable
    tools=[find_slots, book_slot],
    skills=["booking", "rescheduling"],    # names, resolved from the deck's skill roots
    mcp=["calendar", "crm"],               # names, resolved from the deck's MCP file
)

deck = Deck(
    agents=[booking_agent, support_agent],
    workflows=[onboarding_workflow],
    skills=["./skills", "./company-skills"],
    mcp=".mcp.json",
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
Deck.from_project("./.agentdeck")                                   # today's layout, unchanged
```

`from_project` fills `agents`/`workflows`/`skills`/`mcp` by discovery and is **sugar over the
same constructor** — one catalog mechanism underneath, which is #88's own rule.

What `from_project` preserves is the **project layout and discovery convention** —
`agents/<bundle>/agent.py`, `workflows/<bundle>/workflow.py`, `skills/*/SKILL.md`. The *contents*
of those files change with the authoring API; the places AgentDeck looks do not.

## Lifecycle

```
NEW ──build()──► BUILT ──async with──► OPEN ──exit──► CLOSED
```

`build()` validates and is idempotent. **After `BUILT` the catalog is immutable** — otherwise
`deck.build(); agent.tools.append(...)` makes the validation guarantee meaningless. `run`/`stream`
require `OPEN`. `asgi()` owns the OPEN/CLOSED transitions through the ASGI lifespan, so a mounted
deck needs no separate `async with`.

## The ownership rule

> **The Deck owns and closes the infrastructure resources it instantiates from configuration or
> factories. It never assumes ownership of resource instances supplied by user code.**

Stated this way rather than "closes what it constructed", because `from_project()` constructs
`Agent`/`Workflow` objects too — and loading an agent must not imply the deck owns every object
reachable from it. The rule covers the runtime, the stores and the MCP lifecycle precisely, and
leaves a `PostgresEventStore` you passed in for you to close.

## What `Deck` does NOT take

`tools=`, `runtime=`, `store=`… are **not** normal composition. Engine and runtime selection is
infrastructure behind the abstraction, resolved from settings.

The stub engine that `tests/contract/` needs arrives as **`_engines=`**, private by name. A
test-only keyword in the primary constructor is public API whether the reference documents it or
not, and contract tests should not permanently deform the one API users see.

For the same reason **`runtime` and `store` are not documented Deck properties.** Exposing them
would make the infrastructure this plan hides part of the practical public surface on day one.
Reading a run back is `deck.status(...)` / the event-reading methods; anything lower is private
and unstable.

## Phases

### 4a — `authoring/`
`Agent` and `Workflow` constructors, plus the declaration they can be built from. `BaseAgent`
and `BaseWorkflow` move here and are **renamed `AgentDeclaration` / `WorkflowDeclaration`** —
they are declarative *inputs* to `Agent(...)`, and `Base*` misnames that permanently.

```python
Agent(base=BookingDeclaration, instructions=...)   # keyword only
```

No positional base, no `.with(...)`: `Agent(...)` stays the single construction API and the
keyword makes override semantics obvious at the call site.

Node classes (`LoadFileNode`, `AgentNode`) move to `authoring/nodes.py`; `SkillNode` is deleted
(`plan-skills.md`). Everything compiles to `InvocableSpec`, so the Runtime is untouched.

### 4b — skill roots and the registry
`skills=` takes a path or paths. Each root is scanned **direct-child only** — `<root>/<name>/SKILL.md`,
never a recursive `**/SKILL.md` — which is both what the SDK already does and what keeps
shadowing predictable. The roots merge into one registry keyed by directory name. `Agent(skills=["booking"])` resolves against it. Users never
construct a `Skill(...)`. Duplicate names across roots are a `build()` error naming both paths —
one name is one skill, the same rule `PluginRegistry` already applies to bundles.

### 4c — MCP from one file
One MCP file is the single source of truth, replacing the `mcp:` section of `config.yaml` and
`AGENTDECK_MCP_SERVERS`. `Agent(mcp=["calendar"])` resolves names against it; an unknown name is
a `build()` error rather than the current silent drop.

**Filename and shape follow Claude Code**, since compatibility is the point of using a file at
all: `.mcp.json`, with servers under an `mcpServers` object. `McpServerSettings` already mirrors
that block, so the per-server shape is unchanged.

```python
Deck(mcp="./custom-mcp.json")     # any explicit path, any name
Deck.from_project("./.agentdeck") # discovers .mcp.json by convention
```

Convention for the directory project, explicit path for code-first — neither forces a filename
on the other.

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
- **root names are globally unique** across `agents=` and `workflows=` — a collision is an error
  naming both, because `deck.run(name)` is where the two collections converge
- every `Agent(skills=[...])` name resolves in the skill registry, frontmatter included
- every `Agent(mcp=[...])` name resolves in the MCP file
- every workflow an agent references is already registered in `workflows=`
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
- **`status(run_id)` folds a run's events today.** Not an API blocker: the method can fold now
  and move to a store-side projection later without the signature changing — exactly the kind of
  thing the Deck API exists to hide.
