# Review  -  the `Deck` composition plan

**Date:** 2026-08-09 · **Reviewer:** sagi5060 · **Subject:** `plan-phase4-deck.md` · **Status:** closed
**Outcome:** proceed, with ten rulings folded into the plan before 4a/4d were written.

The composition boundary is approved as proposed: **Deck owns composition, authoring produces
specs, Runtime executes specs**  -  separate `agents=`/`workflows=`, path-based `skills=`,
`Deck(context=...)`, validation-only `build()`, no deck-level `tools=`, `from_project()` as sugar
over the same mechanism.

## Rulings

| # | Ruling | Where it landed |
|---|---|---|
| 1 | Root names are one global namespace; a collision is a `build()` error, since `deck.run("foo")` addresses either kind. | `deck.py:234` |
| 2 | Everything in `Deck(workflows=[...])` is registered and root-invocable; an agent references a registered workflow and never adds one to the catalog. | `plan-phase4-deck.md` |
| 3 | Composition freezes after `build()`  -  lifecycle `NEW → BUILT → OPEN → CLOSED`, catalog immutable from `BUILT`, `run`/`stream` require `OPEN`, `asgi()` owns OPEN/CLOSED through ASGI lifespan. | `deck.py`; `plan-phase4-deck.md` ruling 8 |
| 4 | Ownership is "the Deck owns and closes infrastructure resources it instantiates from configuration or factories, and never assumes ownership of resource instances supplied by user code"  -  not "closes what it constructed", which `from_project()` breaks. | `plan-phase4-deck.md` |
| 5 | `base=` is keyword-only (no positional, no `.with(...)`), and `BaseAgent`/`BaseWorkflow` are renamed `AgentDeclaration`/`WorkflowDeclaration`. | `authoring/agent.py`, `authoring/workflow.py`; `plan-phase4-deck.md` ruling 10 |
| 6 | With `Deck(context=…)`, graph compatibility is checked statically at `build()` and instance compatibility at run time, for `run()` and `resume()` alike. | `plan-context-injection.md` ruling 10  -  build-time half only, see divergence below |
|  -  | `build()` validates SKILL.md frontmatter rather than merely finding the file; skill roots are direct-child only, never a recursive `**/SKILL.md`, for predictable shadowing. | `deck.py:451` |
|  -  | MCP config is `.mcp.json` with an `mcpServers` object  -  Claude-Code-compatible by discovery in `from_project()`, any path via `Deck(mcp=…)`. | `deck.py:415-420` |
|  -  | `deck.runtime` / `deck.store` are not documented properties; documenting infrastructure makes it public API on day one. | Neither property exists on `Deck` |
|  -  | A test-only `engines=` is still public API; use `_engines=` or an internal constructor path. | `deck.py:357` |

## Corrections to the plan text

| Claim | Correction |
|---|---|
| `from_project("./.agentdeck")  # today's directory, unchanged` | What is unchanged is the project/discovery *layout*, not the file contents or the API. |
| `status(run_id)` being expensive blocks the phase-4 API | It does not: fold events now, move to a projection later  -  exactly what the Deck API should hide. |

## Divergence from today's code

| Ruling | Divergence |
|---|---|
| 6 | The invocation-time half was deliberately not implemented; `plan-166-delivery.md` slice 4 (2026-08-11) rules a missing context replays with `data=None` instead of refusing. |

## Sources

- Agent Skills specification  -  <https://agentskills.io/specification>
- Claude Code MCP configuration  -  <https://docs.anthropic.com/en/docs/claude-code/mcp>
