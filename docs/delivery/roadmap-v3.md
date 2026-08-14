# Roadmap — finishing v3

**Status:** delivered · **Date:** 2026-08-10 · **Closed:** 2026-08-11 · **Baseline:** `dev` at
`917290e`, with #164 merged (`a5cd353` — `Deck`, `authoring/`, the end of `App`).

Every open issue on the `v3.0.0 — one way to work` milestone, assessed against the tree *after* the
phase-4 merge, and sequenced. **What happens next is `delivery/roadmap-v3.1.md`**, which supersedes
this document as the answer to "what now".

> **Closing note, 2026-08-11 — every wave shipped.** `dev` at `27c4923`. Wave B tagged `v3.0.0b1`;
> Waves 1–5 landed on top of it, in the sequence below. All four rulings held.
>
> Two waves changed shape on contact. **Wave 2** reversed its own order — #156 went first, so
> `AudioBlock` could be the first thing to exercise a minor bump rather than an additive change with
> no way to announce itself. **#166** turned out to be four stacked slices rather than one, because
> the two engines inject a runtime context differently: the OpenAI SDK recognises its wrapper *by
> type*, LangGraph *by parameter name*, so a LangGraph node has to be rewritten rather than merely
> handed a value. `plan-166-delivery.md` records that and the rest of what moved.
>
> **Amended later the same day — v3.0.0 is tagged.** **#219** shipped as the reference application
> (`plan-219-delivery.md`), and **#131** was folded into it by ruling and moved to v3.1 rather than
> run as an open-ended sweep hours before a tag. `v3.0.0` was tagged from `ca03ae8`, the docs site
> deployed with it, and Ask AgentDeck answers on `ask.agentdecksdk.com`.
>
> Everything deferred is either in *Out of v3* below or on a later milestone: #211, #212, #213, #217
> and #218 were filed while executing these waves, and #223, #226, #227 and #228 came out of the
> reference application's friction ledger.

## Relevancy pass

Verified against `dev` at `917290e`, not from the issue text. Nothing was stale enough to close.

| # | Verdict | Note |
|---|---|---|
| #119 | **valid** | build failures still surface a bare exception with no bundle path |
| #120 | **valid, retitled** | the two inboxes survived the rewrite: `pending()` reads the log, `tick()`/`due_resumes()` read the checkpointer (`deck.py:585,608,628`) — and those are public `Deck` API now |
| #122 | **rescope** | the divergence is real, but "v1 reported done" no longer means anything; the question is which behavior is correct for a fan-out whose branch interrupts |
| #130 | **valid** | PR #123's cancel-while-paused fix never got its second review round |
| #131 | **valid** | live-but-heavy code; distinct lens from #71's dead code |
| #132 | **valid** | confirmed: `pyproject.toml` still declares no `license` |
| #155 | **rescope** | 4c already removed `mcp:` and `AGENTDECK_MCP_SERVERS`. What remains is larger: `config.yaml` is a second way to say what `AGENTDECK_*` says, on a milestone called *one way to work* |
| #156 | **valid** | event schema versioning; must land before the wire is frozen |
| #158 | **valid** | confirmed: `clock` still in `composition.py:45` and `runtime/service.py`, accepted and inert |
| #159 | **valid** | `AudioBlock` still absent; the design names audio |
| #161 | **valid, blocked** | needs #159 |
| #162 | **folds into #181** | its two defects are symptoms of tracing being assembled below the composition root |
| #163 | **out of v3** | inventory stale — `BaseSandboxAgent` and `skills/executor.py` are both deleted. The live question is narrower: `core/ports/sandbox.py` and `adapters/caps/sandbox/` have **zero users**, and `authoring/capabilities/` is orphaned. Deferred entirely; #71 deletes the scaffolding |
| #166 | **valid, resequenced** | the last large feature in v3; off the beta path, with #182 deleting the unusable `context=` until it lands |
| #172–#176, #179 | **valid** | the beta findings, from a real run |
| #105 | **valid, moved** | `agentdeck/compat/` is gone; `STRUCTURED_OUTPUT` now lives at `adapters/engines/openai_agents/engine.py:45` |
| #71 | **rescope — premise dead** | written as "runs after the compat facade proves v1 API parity… the public v1 API itself stays". #164 deleted both, and most of its original scope is already executed; what remains is Wave 5's line |

## Rulings taken (2026-08-10)

| # | Ruling | Consequence |
|---|---|---|
| 1 | **Sandboxing leaves v3.** #163 is unmilestoned | Wave 4 disappears and #71 stops waiting on a ruling: v3 should not carry `core/ports/sandbox.py`, `adapters/caps/sandbox/` (zero users) or the orphaned `authoring/capabilities/`, so #71 deletes them. A designed port can return later; that is additive |
| 2 | **Observability moves above the deck, and goes last.** #181 | Tracing gets declared where the deck is declared and owned by its lifecycle, rather than assembled underneath the composition root and started on the first run. **#162 folds in** — both its defects are symptoms of the wrong altitude |
| 3 | **`Context[T]` is the last large feature.** #166 | Everything after it is correctness, cleanup and #181. It also comes **off the beta path**: `Deck(context=...)` is accepted then refused, and **#182 deletes the parameter until it works** — re-adding it is additive |
| 4 | **Multimodal gets a design pass first.** #159, #161 | One plan covering the whole content model before either is implemented, so `AudioBlock` is not bolted on and #161 does not discover the gaps — `delivery/plan-multimodal.md` |

## Waves

Sequenced by what constrains what: anything changing the wire or the config surface lands before
the stable tag freezes them; cleanup runs last because it is the gate.

| Wave | Issues | Why here |
|---|---|---|
| **B** — the beta | #179 #172 #174 #173 #175 #176 #119 #182 | Each one lies to a user, traps a migration, or promises something false: the `tools=[plain_callable]` docs and the `build()` guardrail that would have caught the misuse (one sitting); a declaration-only bundle and a build failure that both fail discovery silently (one sitting); MCP's "boots without it" warning; `Event` as every event's class; no `__version__`; `context=` accepted then refused. Ships `v3.0.0b1` |
| **1** — correctness | #120 #122 #130 | Real divergences, independent and parallelisable. #120 (two approval inboxes) is the deepest and is now public API; #122 needs its v1 comparison dropped first; #130 is the review round PR #123 never got |
| **2** — the wire, before it freezes | the multimodal design pass, #156, #159 + #161, #105 | Each changes the event schema or content model, so each is a breaking change to anyone reading events. **#156 first**, reversing this document's original order. #159 and #161 land in one slice — they share one code path in `_to_sdk_input` — both with the 1 MB decoded inline cap. #105 (retire `openai_agents.structured_output` now `DataBlock` exists) runs in parallel |
| **3** — config, then the last large feature | #155, #166 | #155 is breaking, and #167 (`Preset`, v3.1) is sequenced behind it. #166 restores the `context=` parameter #182 removed and is the last large thing v3 adds |
| **4** — observability, last | #181 (+#162) | By ruling 2 |
| **5** — the pre-stable gate | #71 #131 #132 | #71 cleanup, unblocked by ruling 1: delete `core/ports/sandbox.py`, `adapters/caps/sandbox/` and `authoring/capabilities/`. `Settings.sandbox_env()` and the `SKILL_*` block were deleted early in #155 (same "no callers" finding, surfaced while restructuring the env surface); `observability.sandbox_trace_env()` is the same shape but was left for #71, since #155 could fix its `.host`/`.endpoint` reference in place without deciding whether to delete the function. #131 is live code heavier than it needs to be; #132 starts with `pyproject.toml` declaring no license. Then tag `v3.0.0` |

Hard edges: the multimodal plan gates #159/#161/#156, #161 needs #159, #167 needs #155, and #181
comes last by ruling. Sandboxing gates nothing — it left v3.

## Out of v3

**#163** sandboxing — deferred entirely. Stays open as the design issue; v3 ships none, and #71
deletes the scaffolding rather than preserving it for a design that has not happened.
