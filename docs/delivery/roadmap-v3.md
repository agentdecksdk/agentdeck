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
| #162 | **folds into #181** | its two defects are symptoms of tracing being assembled below the composition root |
| #163 | **out of v3** | its inventory is stale — `BaseSandboxAgent` and `skills/executor.py` are both deleted. The live question is narrower and sharper: `core/ports/sandbox.py` and `adapters/caps/sandbox/` now have **zero users**, and `authoring/capabilities/` is orphaned. Decide whether sandboxing is a v3 capability or gets deleted |
| #166 | **valid, but see below** | `Deck(context=...)` is accepted and then refused at run time |
| #172–#176, #179 | **valid** | the beta findings, from a real run |
| #105 | **valid, moved** | `agentdeck/compat/` is gone; `STRUCTURED_OUTPUT` now lives at `adapters/engines/openai_agents/engine.py:45` |
| #71 | **rescope — premise dead** | written as "runs after the compat facade proves v1 API parity… the public v1 API itself stays". There is no compat facade and no v1 API; #164 deleted both. Most of its original scope is already executed. What actually remains is listed in Wave 5 |

Nothing is stale enough to close. Four need their text corrected before anyone picks them up.

## Rulings taken (2026-08-10)

| # | Ruling | Consequence |
|---|---|---|
| 1 | **Sandboxing leaves v3.** #163 is unmilestoned | Wave 4 disappears and #71 stops waiting on a ruling. v3 should not carry `core/ports/sandbox.py` and `adapters/caps/sandbox/` with **zero users**, nor the orphaned `authoring/capabilities/` — #71 deletes them. A designed port can return later; that is additive |
| 2 | **Observability moves above the deck, and goes last.** #181 | Tracing gets declared where the deck is declared and owned by its lifecycle, rather than assembled underneath the composition root and started on the first run. **#162 folds into it** — both its defects are symptoms of the wrong altitude, so fixing them in place would mean fixing the same ordering twice |
| 3 | **`Context[T]` is the last large feature.** #166 | Everything after it is correctness, cleanup and #181. It also comes **off the beta path**: `Deck(context=...)` is accepted then refused, which is a false promise, but the fix need not be the eight-step epic — **#182 deletes the parameter until it works**, and re-adding it is additive |
| 4 | **Multimodal gets a design pass first.** #159, #161 | One plan covering the whole content model before either is implemented, so `AudioBlock` is not bolted on and #161 does not discover the gaps |

### What the multimodal plan has to answer

- the full block set, and whether it is closed — `text`, `image`, `resource`, `audio`, `data`, `unknown`
- how each block reaches each engine, and what an engine does with one it cannot express: raise,
  drop-with-event, or degrade (today `_to_sdk_input` raises for anything non-text)
- inbound versus outbound symmetry — can an agent *return* audio, or only receive it?
- how a block survives the event log and the SSE wire, and what an old reader does with a kind it
  has never seen (`UnknownBlock` exists — confirm it covers this)
- binary payloads: inline base64, a resource reference, or both, and the size threshold that decides
- whether this is one schema change or two, given it touches the envelope #156 freezes

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
| #182 | `Deck(context=...)` is accepted then refused — delete it until #166 lands |

#179 and #172 are one sitting: the doc correction and the guardrail that would have caught the
misuse. #174 and #119 are also one sitting — both are "discovery failed and told you nothing".

### Wave 1 — correctness · 3 issues

Real divergences, none of them cosmetic. Independent of each other, parallelisable.

- **#120** two approval inboxes — the deepest of the three, and now public API
- **#122** fan-out interrupt reporting (rescope first: drop the v1 comparison)
- **#130** the confirming review round PR #123 never got

`#162` is no longer here — it folds into #181 in Wave 4.

### Wave 2 — the wire, before it freezes · 4 issues

Everything here changes the event schema or content model. It must precede the stable tag, and it
should precede a wide beta audience, because each one is a breaking change to anyone reading
events.

1. **the multimodal design pass** — one plan for the whole content model (see the ruling above).
   Nothing below starts until it exists
2. **#159** `AudioBlock`, then **#161** multimodal input
3. **#156** event schema versioning — freeze the envelope deliberately, informed by what the plan
   decided about block kinds
4. **#105** retire `openai_agents.structured_output` now `DataBlock` exists

### Wave 3 — the config surface, then the last large feature · 2 issues

- **#155** settings restructure — breaking, and #167 (`Preset`, v3.1) is explicitly sequenced
  behind it
- **#166** `Context[T]` injection — **the last large thing v3 adds.** It restores the `context=`
  parameter #182 removed. Everything after this wave is correctness, cleanup and observability

### Wave 4 — observability, last · 1 issue

- **#181** observability declared above the deck and owned by its lifecycle, instead of assembled
  underneath the composition root and started on the first run. **#162 folds in** — its two
  defects are symptoms of that altitude, and fixing them in place would mean fixing the same
  ordering twice

### Wave 5 — the pre-stable gate · 3 issues

Last substantive work before `v3.0.0`.

- **#71** cleanup — rescoped, and no longer blocked on a sandboxing ruling. With sandboxing out of
  v3 the answer is settled by default: delete `core/ports/sandbox.py` and `adapters/caps/sandbox/`
  (zero users), `authoring/capabilities/` (orphaned), `Settings.sandbox_env()` and the `SKILL_*`
  block (no callers), and `observability.sandbox_trace_env()` (no callers)
- **#131** simplification — live code heavier than it needs to be
- **#132** professional gaps — starting with `pyproject.toml` declaring no license, which every
  metadata reader currently sees as unlicensed

Then tag `v3.0.0`.

## Dependency graph

```
Wave B ──► v3.0.0b1
                │
   ┌────────────┴─────────────┬─────────────────┐
   ▼                          ▼                 ▼
Wave 1                 multimodal plan       #155 ─► #167
#120 #122 #130          │        │            (v3.1)
                        ▼        ▼                │
                   #159►#161   #156  #105         ▼
                        │        │              #166
                        └────────┴────────────────┤
                                                  ▼
                                          Wave 4 — #181 (+#162)
                                                  ▼
                                    Wave 5 — #71 · #131 · #132
                                                  ▼
                                               v3.0.0
```

Hard edges: the multimodal plan gates #159/#161/#156, #161 needs #159, #167 needs #155, and #181
comes last by ruling. Sandboxing no longer gates anything — it left v3.

## Out of v3

- **#163** sandboxing — deferred entirely. Stays open as the design issue; v3 ships none, and #71
  deletes the scaffolding rather than preserving it for a design that has not happened.

## Housekeeping before anyone starts

Four issues need their text corrected so the next person does not implement against a tree that no
longer exists: **#71** (premise dead), **#163** (inventory stale), **#122** (v1 comparison moot),
**#105** (file moved). #155 already carries a re-scope comment.
