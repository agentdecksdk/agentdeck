# Review — the `Deck` composition plan

**Date:** 2026-08-09 · **Reviewer:** sagi5060 · **Subject:** `plan-phase4-deck.md`
**Outcome:** proceed, with ten rulings folded into the plan before 4a/4d are written.

Kept as a record because the rulings below are the reason the plan reads the way it does, and
because several of them close holes that would otherwise have become accidental API decisions.

## Verdict

A strong v3 direction. The composition boundary is much clearer than an `App`/runtime-heavy API:
**Deck owns composition, authoring produces specs, Runtime executes specs.**

Kept essentially as proposed: separate `agents=`/`workflows=`, path-based `skills=`,
`Deck(context=...)`, validation-only `build()`, no deck-level `tools=`, and `from_project()` as
sugar over the same mechanism.

## Rulings added before implementation

**1. One global root namespace.** Since `deck.run("foo")` can address either an agent or a
workflow, root names must be globally unique. Collision is a `build()` error — otherwise the
separate collections become ambiguous at the exact point where they converge.

**2. Registered workflow vs agent ability.** "An agent may hold a workflow as an ability" was
ambiguous. Everything in `Deck(workflows=[...])` is registered and root-invocable; agents
reference registered workflows by object or name, and never secretly introduce additional
workflows into the catalog.

**3. Freeze composition after `build()`.** Idempotence is not enough — define the lifecycle
`NEW → BUILT → OPEN → CLOSED`. After `BUILT` the catalog is immutable, or
`build(); agent.tools.append(...)` makes the validation guarantee meaningless. `async with` may
call `build()`; `run`/`stream` require `OPEN`; `asgi()` owns OPEN/CLOSED through ASGI lifespan.

**4. Rewrite the ownership rule.** "Closes what it constructed" is too broad, because
`from_project()` also constructs `Agent`/`Workflow` objects. Say instead:

> The Deck owns and closes infrastructure resources it instantiates from configuration or
> factories. It never assumes ownership of resource instances supplied by user code.

That covers runtime/store/MCP lifecycle precisely without implying that loading an agent means
the Deck now owns every object reachable from it.

**5. `base=` keyword only**, no positional base and no `.with(...)`, keeping `Agent(...)` as the
one construction API. And rename `BaseAgent` → `AgentDeclaration`, `BaseWorkflow` →
`WorkflowDeclaration`: in a breaking v3, calling something `Base*` when it is a declarative
*input* to `Agent(...)` is unnecessary conceptual debt.

**6. Define context runtime enforcement.** With `Deck(context=MiddleContext)`, `run(context=None)`
needs explicit semantics: every root whose graph requires context must receive a compatible
instance, and failure happens before execution. `resume()` applies the same rule. Graph
compatibility stays static at `build()`; instance validation is run-time.

## Protocol details

**Skills.** The registry design is correct, including using the directory name as the identifier
— the Agent Skills specification requires `SKILL.md` to carry a `name` and requires it to match
the parent directory, so `Agent(skills=["booking"])` is a natural mapping. `build()` should
therefore validate the frontmatter, not merely discover the file. Roots should be **direct-child
only**, not a recursive `**/SKILL.md` search, which gives far more predictable shadowing.

**MCP.** If Claude-Code-compatible project configuration is the intent, the conventional filename
is **`.mcp.json`** with an `mcpServers` object. So: `Deck(mcp="./custom-mcp.json")` for any
explicit path, and `Deck.from_project()` discovering `.mcp.json` by convention — compatibility
without forcing the filename on code-first users.

## Two API leaks to remove

**`deck.runtime` / `deck.store` should not be documented properties.** The plan says runtime and
store are infrastructure behind the abstraction, then exposes them; that makes them part of the
practical public API on day one regardless of what the reference says.

**A test-only `engines=` is still public API.** Prefer `_engines=` or an internal constructor
path. Contract tests should not permanently deform the primary constructor.

## Document corrections

`from_project("./.agentdeck")  # today's directory, unchanged` contradicts "every
`.agentdeck/agents/*/agent.py` changes shape". What is unchanged is the **project/discovery
layout**, not the file contents or the API.

`status(run_id)` being expensive today is **not** a phase-4 API blocker: it can fold events now
and move to a projection later. That is exactly the kind of thing the Deck API should hide.

## Result

```
Authoring                  Agent / Workflow / Nodes
    ↓ compile              InvocableSpec
Deck                       composition · validation · lifecycle · invocation
    ↓
Runtime                    execution only
```

The most important decisions were **catalog/root identity** and **lifecycle immutability**;
both are resolved above. Everything else follows without changing the core shape.

## Sources

- Agent Skills specification — <https://agentskills.io/specification>
- Claude Code MCP configuration — <https://docs.anthropic.com/en/docs/claude-code/mcp>
