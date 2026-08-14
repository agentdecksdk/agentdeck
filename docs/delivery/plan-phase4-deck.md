# Plan — phase 4: `Deck`, the v3 composition API

**Delivered** in v3.0.0 · **Date:** 2026-08-09 · Resolves #88, blocks phases 5–6, and supersedes the
Option A recommendation in `decision-v3-entry-point.md` where they differ.

## Rulings taken (2026-08-09)

| # | Question | Ruling |
|---|---|---|
| 1 | How is an agent declared? | **`Agent(...)` constructor**, which may take a declaration as its base and override on construction |
| 2 | Deck-level `mcp=`, given the process-wide registry | **One MCP file is the single source of truth at deck level.** A deck per tenant is a *process* per tenant, so the global registry is acceptable and is not rework for this phase |
| 3 | `agents=` / `workflows=` or one `invocables=` | **Separate.** Both are executable roots reachable by `deck.run`, *and* an agent may hold a workflow as an ability |
| 4 | An explicit `build()` | **Yes** — idempotent, validates only, opens nothing; `async with` calls it if you did not |
| 5 | Deck declares the context type | **Yes** — `Deck(context=MiddleContext)`; see `plan-context-injection.md` |
| 6 | Root name identity | **One global root namespace.** `deck.run(name)` addresses either collection, so an agent and a workflow sharing a name is a `build()` error naming both |
| 7 | A workflow as an agent's ability | **Everything in `workflows=` is registered and root-invocable.** An agent references a *registered* workflow by name or object, and can never introduce one the catalog does not hold |
| 8 | Lifecycle | **`NEW → BUILT → OPEN → CLOSED`.** The catalog is immutable after `BUILT` or the validation guarantee is worthless; `run`/`stream` require `OPEN`; `asgi()` owns OPEN/CLOSED through the ASGI lifespan |
| 9 | `Agent(base=…)` mechanics | **`base=` keyword only.** No positional base, no `.with(...)` — `Agent(...)` stays the one construction API |
| 10 | Declaration naming | **`BaseAgent` → `AgentDeclaration`, `BaseWorkflow` → `WorkflowDeclaration`.** In a breaking release, calling something `Base*` when it is a declarative *input* to `Agent(...)` is conceptual debt with no upgrade path later |
| 11 | Sandboxing | **A future ability, currently disabled and out of scope (#163).** No agent, tool or skill is sandboxed in v3; `BaseSandboxAgent` goes with `agents/` and nothing replaces it in `authoring/` |
| 12 | Capability wrappers | **A subsystem with real behavior gets an object** — `Skills(...)`, `MCP(...)` — so its options live there rather than on `Deck` (`deck-capability-wrapper-pattern.md`). A bare path still works: `Deck` coerces `str`/`Sequence[str]`. Executable roots are **not** wrapped; there is no `Agents([...])` |
| 13 | Who closes a capability | **The Deck.** A wrapper is a *declaration*, inert until the deck opens, so the deck instantiated the connections from configuration and closes them — the ownership rule applies with no exemption for capability arguments |

## Target shape

*Amended 2026-08-10 (#179): `tools=[find_slots, book_slot]` below reads as plain callables, which was
never implemented and is not today's contract — a tool is an Agents SDK tool object
(`@function_tool`-wrapped), and bare callables are a future ability. Left as this record originally
read; `docs-site/content/reference/definitions.mdx` states the actual contract.*

*Amended 2026-08-11 (#166): the future ability arrived, so the block below is literal rather than
aspirational. `tools=` takes plain callables and compiles them, because a parameter annotated
`Context[T]` cannot be pre-decorated — `@function_tool` would put it in the model-visible schema. A
pre-built SDK tool object is still accepted as engine-native.*

```python
booking_agent = Agent(name="booking",
    instructions=booking_instructions,     # str or a Context-taking callable
    tools=[find_slots, book_slot],
    skills=["booking", "rescheduling"],    # names, resolved from the deck's skill roots
    mcp=["calendar", "crm"])               # names, resolved from the deck's MCP file

deck = Deck(agents=[booking_agent], workflows=[onboarding_workflow], context=MiddleContext,
            skills=["./skills", "./company-skills"],   # coerced to Skills(…); Skills(…, validate=False) for options
            mcp=".mcp.json")                           # coerced to MCP(…)
```

`Deck.from_project("./.agentdeck")` is sugar over the same constructor — #88's own rule — and
preserves the **layout and discovery convention**: `agents/<bundle>/agent.py`,
`workflows/<bundle>/workflow.py`, `skills/*/SKILL.md`. The *contents* of those files change with the
authoring API; the places AgentDeck looks do not.

## The ownership rule

> **The Deck owns and closes the infrastructure resources it instantiates from configuration or
> factories. It never assumes ownership of resource instances supplied by user code.**

Not "closes what it constructed": `from_project()` constructs `Agent`/`Workflow` objects too, and
loading an agent must not imply owning every object reachable from it. A `PostgresEventStore` you
constructed is still yours to close.

**What `Deck` does NOT take.** `tools=`, `runtime=`, `store=` are infrastructure resolved from
settings, not composition. The stub engine `tests/contract/` needs arrives as **`_engines=`**, private
by name, because a test-only keyword in the primary constructor is public API whether the reference
documents it or not — and for the same reason `runtime` and `store` are not documented `Deck`
properties. Reading a run back is `deck.status(...)` and the event-reading methods.

## Phases

| # | Phase | Content |
|---|---|---|
| 4a | `authoring/` | `Agent`/`Workflow` constructors plus the declaration they build from; `BaseAgent`/`BaseWorkflow` move here under ruling 10's names; `Agent(base=BookingDeclaration, instructions=...)` keyword-only. Node classes (`LoadFileNode`, `AgentNode`) → `authoring/nodes.py`; `SkillNode` deleted (`plan-skills.md`). Everything compiles to `InvocableSpec`, so the Runtime is untouched |
| 4b | Skill roots and the registry | `skills=` takes a path, paths or `Skills(...)`; each root scanned **direct-child only** (`<root>/<name>/SKILL.md`, never recursive), merged into one registry keyed by directory name. `Agent(skills=["booking"])` resolves against it; users never construct a `Skill(...)`; duplicate names across roots are a `build()` error naming both paths |
| 4c | MCP from one file | `mcp=` takes a path or `MCP(...)`, replacing `config.yaml`'s `mcp:` section and `AGENTDECK_MCP_SERVERS`; an unknown `Agent(mcp=[…])` name is a `build()` error, not today's silent drop. **Filename and shape follow Claude Code** — `.mcp.json`, servers under `mcpServers`, which `McpServerSettings` already mirrors. Convention for the directory project, explicit path for code-first. **This reverses #78** deliberately, and interacts with **#155**: the env-surface restructure must account for `mcp:` leaving `config.yaml` |
| 4d | `Deck` itself | The class, both constructors, `build()`, the lifecycle. Roughly `app.py`'s composition-root half moved almost verbatim, plus the event-stream reducers (`_turn_result`, `_workflow_result`) unchanged. `pause`/`cancel`/`status` are new flat-verb names over `Runtime.signal` and the store's status projection |
| 4e | serve, unchanged behavior | `agentdeck serve` becomes `Deck.from_project()` + `deck.asgi()`. The HTTP contract and `tests/golden/` do not move — this is a Python-API change only, and the wire staying byte-identical is what proves it |
| 4f | deletion | `agents/` and `workflows/` deleted, `app.py` reduced to the composition root or removed. No re-export facades: v1's Python API is dropped, per the cutover ruling |

## What `build()` checks

It opens no connections, starts no MCP server and touches no network, which is what makes it usable
as `agentdeck check` in CI:

- every agent/workflow compiles to an `InvocableSpec`
- **root names are globally unique** across `agents=` and `workflows=`, a collision naming both
- every `Agent(skills=[...])` name resolves in the skill registry, frontmatter included
- every `Agent(mcp=[...])` name resolves in the MCP file
- every workflow an agent references is already registered in `workflows=`
- the engine each spec needs is registered
- **context compatibility across the whole graph** — `plan-context-injection.md`

## Deferred, and risks

| | |
|---|---|
| Deferred: splitting `Execution` out of `RunContext` | Ruled *yes, but later* — it re-threads every port and this phase ships without it |
| Deferred: instance-scoping `MCPLifecycle` | Unnecessary while a deck is a process |
| Deferred: `tools=` at deck level | Agents own their tools; there is no second place to put them |
| Risk: the authoring change *is* the migration | Every `.agentdeck/agents/*/agent.py` changes shape, so the golden fixture project is rewritten — fixtures move even though the wire does not, and review must not confuse the two |
| Risk: 4c reverses a shipped decision (#78) | If #155 lands first the two conflict; sequence them |
| Risk: `status(run_id)` folds a run's events today | Not an API blocker — it can move to a store-side projection later without the signature changing, which is what the Deck API exists to hide |
