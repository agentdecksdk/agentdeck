# Roadmap — finishing v3

**Status:** proposed · **Date:** 2026-08-10 · **Baseline:** `dev` at `917290e`, with #164 merged
(`a5cd353` — `Deck`, `authoring/`, the end of `App`).

Every open issue on the `v3.0.0 — one way to work` milestone, assessed against the tree as it
stands *after* the phase-4 merge, and sequenced. Several were written before that merge and
describe files that no longer exist; those are called out rather than silently carried.

## Relevancy pass

Verified against `dev` at `917290e`, not from the issue text.

| # | Verdict | Note |
|---|---|---|
| #119 | **valid** | build failures still surface a bare exception with no bundle path |
| #120 | **valid, retitled** | the two inboxes survived the rewrite: `pending()` reads the log, `tick()`/`due_resumes()` read the checkpointer (`deck.py:585,608,628`). Worse than when filed — those are public `Deck` API now |
| #122 | **rescope** | the divergence is real, but "v1 reported done" is no longer a comparison that means anything. The question is now simply which behavior is correct for a fan-out whose branch interrupts |
| #130 | **valid** | PR #123's cancel-while-paused fix never got its second review round |
| #131 | **valid** | live-but-heavy code; distinct lens from #71's dead code |
| #132 | **valid** | confirmed: `pyproject.toml` still declares no `license` |
| #155 | **rescope** | 4c already removed `mcp:` and `AGENTDECK_MCP_SERVERS`. What remains is larger: `config.yaml` is a second way to say what `AGENTDECK_*` says, on a milestone called *one way to work* |
| #156 | **valid** | event schema versioning; must land before the wire is frozen |
| #158 | **valid** | confirmed: `clock` still in `composition.py:45` and `runtime/service.py`, accepted and inert |
| #159 | **valid** | `AudioBlock` still absent; the design names audio |
| #161 | **valid, blocked** | needs #159 |
| #162 | **valid** | telemetry client ordering and orphan spans, deferred from #152 |
| #163 | **rescope** | its inventory is stale — `BaseSandboxAgent` and `skills/executor.py` are both deleted. The live question is narrower and sharper: `core/ports/sandbox.py` and `adapters/caps/sandbox/` now have **zero users**, and `authoring/capabilities/` is orphaned. Decide whether sandboxing is a v3 capability or gets deleted |
| #166 | **valid, but see below** | `Deck(context=...)` is accepted and then refused at run time |
| #172–#176, #179 | **valid** | the beta findings, from a real run |
| #105 | **valid, moved** | `agentdeck/compat/` is gone; `STRUCTURED_OUTPUT` now lives at `adapters/engines/openai_agents/engine.py:45` |
| #71 | **rescope — premise dead** | written as "runs after the compat facade proves v1 API parity… the public v1 API itself stays". There is no compat facade and no v1 API; #164 deleted both. Most of its original scope is already executed. What actually remains is listed in Wave 5 |

Nothing is stale enough to close. Four need their text corrected before anyone picks them up.

## The one scope call worth making now

**#166 (context injection) should come off the beta path.**

It is on `blocks-beta` for an honesty reason, not a feature reason: `Deck(context=MiddleContext)`
is accepted at construction and then raises if you actually pass a context to `run()`. A
constructor parameter that cannot be used is a false promise, and a beta should not ship one.

But the fix does not have to be the epic. `plan-context-injection.md` is an eight-step effort
touching `Context[T]`, callable analysis and a bridge per engine SDK. The cheap honest move is to
**remove `context=` from the constructor until it works** — deleting a parameter nobody can
successfully use breaks nothing, and adding it back later is additive rather than breaking.

That turns a blocking epic into a ten-minute deletion, and moves #166 to Wave 3 where it belongs.

## Waves

Sequenced by what constrains what: anything that changes the wire or the config surface must land
before the stable tag freezes them; cleanup runs last because it is the gate.

### Wave B — the beta · 8 issues, all small

Ship `v3.0.0b1` when these are done. The test each one passes: *does it lie to a user, trap a
migration, or promise something false?*

| # | Why it blocks |
|---|---|
| #179 | three documents promise `tools=[plain_callable]`, which is not the contract |
| #172 | `build()` accepts a tool it cannot compile — the guardrail for exactly that misuse |
| #174 | a declaration-only bundle yields an empty catalog silently — the v1→v3 migration trap |
| #173 | MCP warns "boots without it" during `build()`, in the wording of the silent drop v3 removed |
| #175 | every streamed event's class is `Event`, so there is nothing obvious to switch on |
| #176 | no `__version__` — the first line anyone types, and the one a bug reporter needs |
| #119 | a build failure names no bundle, which is the same wound as #174 |
| #166 | reduced to "remove `context=` until it works" per the scope call above |

#179 and #172 are one sitting: the doc correction and the guardrail that would have caught the
misuse. #174 and #119 are also one sitting — both are "discovery failed and told you nothing".

### Wave 1 — correctness · 4 issues

Real divergences, none of them cosmetic. Independent of each other, parallelisable.

- **#120** two approval inboxes — the deepest of the four, and now public API
- **#122** fan-out interrupt reporting (rescope first: drop the v1 comparison)
- **#130** the confirming review round PR #123 never got
- **#162** telemetry client ordering and orphan spans

### Wave 2 — the wire, before it freezes · 4 issues

Everything here changes the event schema or content model. It must precede the stable tag, and it
should precede a wide beta audience, because each one is a breaking change to anyone reading
events.

1. **#159** `AudioBlock` — unblocks the next
2. **#161** multimodal input (blocked on #159)
3. **#156** event schema versioning — freeze the envelope deliberately
4. **#105** retire `openai_agents.structured_output` now `DataBlock` exists

### Wave 3 — the config surface and the deferred epic · 2 issues

- **#155** settings restructure — breaking, and #167 (`Preset`, v3.1) is explicitly sequenced
  behind it
- **#166** context injection proper, once it is no longer a beta blocker

### Wave 4 — the design ruling · 1 issue

- **#163** sandboxing. Now a narrower question than when filed: `core/ports/sandbox.py` and
  `adapters/caps/sandbox/` have zero users, and `authoring/capabilities/` is orphaned. Either
  sandboxing becomes a designed v3 capability, or all of it is deleted. **This must be decided
  before Wave 5**, because it determines what Wave 5 deletes.

### Wave 5 — the pre-stable gate · 3 issues

Last substantive work before `v3.0.0`.

- **#71** cleanup — rescope to what actually remains: `authoring/capabilities/` (orphaned),
  `Settings.sandbox_env()` and the `SKILL_*` block (no callers), `observability.sandbox_trace_env()`
  (no callers), and whatever #163 rules on
- **#131** simplification — live code heavier than it needs to be
- **#132** professional gaps — starting with `pyproject.toml` declaring no license, which every
  metadata reader currently sees as unlicensed

Then tag `v3.0.0`.

## Dependency graph

```
Wave B ──► v3.0.0b1
                │
   ┌────────────┼────────────┬──────────────┐
   ▼            ▼            ▼              ▼
Wave 1     #159 ─► #161    #155 ─► #167   #163 (ruling)
(4 fixes)  #156   #105     (v3.1)           │
   │            │            │              │
   └────────────┴────────────┴──────────────┘
                     ▼
              Wave 5 — #71 · #131 · #132
                     ▼
                  v3.0.0
```

Only three hard edges: #161 needs #159, #167 needs #155, and Wave 5 needs #163's ruling.
Everything else can run in any order or in parallel.

## Housekeeping before anyone starts

Four issues need their text corrected so the next person does not implement against a tree that no
longer exists: **#71** (premise dead), **#163** (inventory stale), **#122** (v1 comparison moot),
**#105** (file moved). #155 already carries a re-scope comment.
