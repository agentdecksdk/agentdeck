# Milestone 0 — Findings (the go/no-go checkpoint)

The "at the finish" writeup `milestone-0-walking-skeleton.md` §6 asks for — falsifier review, schema
diff, learning note, decision log, disposal call — because they are one review, not four.
**Closed:** accepted 2026-08-05, issue #57. **Relates to:** `agentdeck-v2-architecture.md`,
`adr-d5-two-stores.md`, `epic-agentdeck-v2-core.md`, `prompts/pr1-event-schema-prompt.md`. Demo
artifact: `scripts/m0_demo.py` (§9).

## 1. Verdict

**GO.** None of the six falsifiers in milestone §6 fired; the epic proceeds to Story 2 hardening
rather than redesign. The one open design finding — `origin`'s scope — is ruled in §3 (Option B,
2026-08-05), so it is a documented contract, not a gap.

## 2. Falsifier review — evidence, not impression

| # | Falsifier | Verdict | Evidence |
|---|---|---|---|
| 1 | Any consumer needed to know which engine produced an event | **Did not fire** | `grep -rn "openai-agents\|langgraph\|OpenAIAgentsEngine\|LangGraphEngine" agentdeck/surfaces/` is empty, and the `.importlinter` contract `surfaces-are-adapter-free` makes it structural — `agentdeck.surfaces` may not import `agents`, `langgraph`, `redis` or `agentdeck.adapters` at all, enforced by `make lint-imports`. `surfaces/serve/app.py::build_app` and `surfaces/cli/chat.py::render` are the same functions for UC1 (openai-agents) and UC2 (langgraph); `scripts/m0_demo.py` drives both through them |
| 2 | The schema needed a new **required** field after UC1 events were already persisted | **Did not fire** | `git show 40761bd -- agentdeck/core/events.py` (#58, UC2) and `git show edc6e4f -- …` (#59, UC3) are both empty diffs; `git log --follow` shows `events.py` unchanged since PR #49 created it (`e31bf96`) |
| 3 | UC2 required editing any UC1 consumer | **Did not fire** | `git show 40761bd` and `git show edc6e4f` against `surfaces/serve/app.py` and `surfaces/cli/chat.py` are both empty. UC2 added `surfaces/serve/workflows.py` (`/pending`+`/resume`) instead of touching `app.py`; UC3's cancel wiring lives in `Runtime._with_gate`, which rebinds `ctx.gate` before an engine sees it. `test_uc2_claim_pipeline.py` and `test_uc3_slowpoke.py` run the unmodified `build_app` and `render` |
| 4 | Transcript fidelity was only achievable with byte-level normalization | **Did not fire** | `test_uc1_handoff.py::_message_transcript_from_log` / `_message_transcript_from_session` compare with plain `==` on `list[tuple[str, str]]` — no strip, lowercase, whitespace collapse or regex anywhere in the file. `test_langgraph_transcript_fidelity` compares `aget_state().values` against the log's shallow-merged `node.updated` patches with plain dict `==`. Both run in `make check` |
| 5 | Exactly-one-terminal-event needed consumer-side workarounds (timeouts, dedupe) | **Did not fire** | The invariant is enforced inside `Runtime.run`/`resume`: a terminal payload breaks the read loop, and all four exit paths (terminal, suspend, exception, abandoned-consumer `GeneratorExit`) close the run in the log first. No consumer has a timeout or dedupe workaround — the only `timeout=` hits are `subprocess.run` calls in the test harness — and `check_terminal()` is only ever a test assertion |
| 6 | The gap-detection test could not be made reliable | **Did not fire** | `test_uc3_chaos_gap_detection_recovers_from_store` drops a `seq` from an already-materialized `list[Event]` in a synchronous generator, so the loss is deterministic by construction and cannot flake. The mandated 20×-loop (`test_uc3_cancel_lands_at_next_safe_point_stable_across_20_runs`) asserts `check_contiguous(events) == []` on every trial, with no failures observed across repeated `make check` runs |

## 3. The open design finding: is `origin` invocable-scoped or sub-agent-scoped?

`Runtime._record` stamps `origin = spec.name`, the top-level `InvocableSpec` the caller addressed, so
after an internal openai-agents handoff `ClaimsAgent`'s own messages still carry
`origin="FrontDesk"` — `message_id` keeps each message distinct, but `origin` alone cannot say which
sub-agent spoke. Not a schema divergence (§4): the field's shape and the "never the engine" rule are
honored exactly, and this is a behavioral choice design time never made, the worked example never
having run a handoff. `tests/test_uc1_handoff.py` asserts the shipped behavior on purpose.

| option | mechanism | cost |
|---|---|---|
| A — engine-supplied speaker attribution | An optional `speaker: str \| None` on the content-bearing payloads (`TextDelta`, `ThoughtDelta`, `MessageCompleted`, arguably the tool-call pair), carrying the sub-agent the engine reports; additive under D8, no `v` bump, payload-only so D9's closed envelope is untouched | A schema PR (coding-standards §7) plus both adapters' translation code and new golden snapshots; langgraph needs only a naming reconciliation, since `node.updated.node` already plays this role. Old events simply lack the field |
| B — redefine "speaker" as the invocable | `origin` unchanged; the limit written into the schema docstring and the architecture doc. Finer attribution is built from `message_id` sequencing plus, for openai-agents, the namespaced `openai_agents.handoff` `custom` events | Zero code. A `FrontDesk` → `ClaimsAgent` handoff renders as one labeled speaker in every UI built on the canonical stream, and a consumer buying finer attribution from the engine-specific events is knowingly trading the "never the engine" property |

**RULING (2026-08-05, maintainer decision on issue #57): Option B.** "Speaker" means *the invocable
the caller addressed*, never *the SDK's internal sub-agent*, so one label across an internal handoff
is correct behavior and `test_uc1_handoff.py` documents a contract, not a gap. Option A stays the
designated fix, as its own schema PR, if a concrete UI story ever needs sub-agent attribution. The
contract is stated in `core/events.py`'s `origin` docstring and `agentdeck-v2-architecture.md` §4.2.

## 4. Schema-as-built diff

`agentdeck/core/events.py` + `core/content.py` against design doc §4.2 and the nine review decisions
(`00-project-index.md` §5) plus decisions A=contiguous `seq` and B=full-text `message.completed`.
**Every envelope field, every kind, and all nine decisions plus A/B match the design doc and the PR
#1 prompt exactly.** The divergences are strengthenings inside that contract, not wire-format changes:

| Divergence | As designed | As built | Justification |
|---|---|---|---|
| Model mutability | Plain `BaseModel`s | `CoreModel` sets `frozen=True` for every payload and content block | coding-standards §4: mutating an event after construction is a bug by definition; one sink cannot mutate what another holds |
| `extra` policy location | "Pick one, apply uniformly" (mechanism unspecified) | One shared `CoreModel(extra="ignore", frozen=True)` in `content.py`, inherited by every payload | One place implements and can be audited for the forward-compat contract |
| `UnknownEvent`'s own `extra` policy | Not called out | Overrides to `extra="forbid"` | It shares the discriminated union with the known payloads, so laxness would let a malformed *known* payload validate as `UnknownEvent` instead of raising |
| Integer field strictness | `seq`, `result_size`, token counts: `int` | `NonNegativeInt` | Matches the domain invariant at the type level; a JSON integer either way |
| `parse_event`'s unknown-kind detection | Unknown `kind` → `UnknownEvent` payload | Also accepts an already-wrapped `{kind, raw_payload}` shape as itself an `UnknownEvent` | Required for round-trip: a stored `UnknownEvent` must survive `model_dump_json` → `parse_event` → equality. Tested in `tests/core/test_events.py` |
| `Budget`'s naming | An inline anonymous `{max_usd, max_tokens} \| None` on `RunContextSnapshot` | A named `Budget` class, reused | Matches the design doc's own §4.3 usage; same wire format |

## 5. What the skeleton taught, ranked by "expensive to learn in Phase 2"

| # | Lesson | Cost of learning it later |
|---|---|---|
| 1 | The two-store rule (ADR-D5) needed a real multi-turn handoff to prove, and it held | Before UC1 it was an argument backed by a worked example, not a test; discovering after Story 2 that log-only reconstruction degrades multi-turn context would have meant rewriting the core abstraction under every consumer |
| 2 | `EnginePort`/`Runtime` covers two structurally different execution models with zero consumer-visible difference | The epic's highest-risk bet, now measured rather than hoped: `surfaces/serve/app.py` and `surfaces/cli/chat.py` are byte-identical across UC1 and UC2 (§2, falsifiers 1 and 3) |
| 3 | LangGraph's bare-`dict` state schema silently breaks `node.updated`'s documented shallow-merge contract | `StateGraph(dict)` treats state as one opaque channel, so a node's return *replaces* it. Found only because a UC2 fixture used a bare `dict`; every `StateGraph` in this codebase must use a `TypedDict` or pydantic model |
| 4 | A durable LangGraph checkpointer binds to the event loop that first constructed it | `_sqlite_saver`/`_postgres_saver` are `@cache`d per URL and hold a loop-bound lock — fine for one long-lived loop, a mystifying production incident for anything calling `asyncio.run()` twice against the same durable graph |
| 5 | `httpx.ASGITransport` cannot interleave a live signal into an in-flight SSE response | It runs the whole ASGI call before returning bytes, which is why UC3's chat-route cancellability is proven by architecture (`Runtime._with_gate`) — and why Story 3's acceptance criteria need a real ASGI server rather than an untestable AC found mid-story |
| 6 | The double-resume guard is process-local, and that boundary is easy to miss | An `asyncio.Lock` keyed by `run_id` inside one `Runtime` is correct for two callers racing one process and silently wrong for two processes over one store — the shape a multi-worker deployment has. A store-level CAS primitive is needed before Redis-backed multi-worker resume is safe |

## 6. Decision log — ad hoc calls promoted to documented contract

| Decision | Made ad hoc during | Where it now lives |
|---|---|---|
| Interrupt-first safe-point contract: an interrupting node calls `interrupt()` as its first statement, so re-entry on resume repeats nothing | UC2 (#53) | `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment |
| Double-resume guard is process-local (`asyncio.Lock` per `run_id`, not a store-level CAS) | UC2 (#53) | Same amendment; re-grounded for Story 3 in the epic amendment (§7) |
| `Runtime._with_gate` rebinds `ctx.gate` only when built with a `ControlPort`, so UC1's consumers never learn control exists | UC3 (#54) | `agentdeck-v2-architecture.md` §8's 2026-08-05 amendment |
| `StateGraph` schemas in this codebase must be `TypedDict`/pydantic, never a bare `dict` | UC2 fixture debugging (#58) | This document (§5, point 3); a lint or runtime check is future work |
| Durable checkpointers are cached per URL and bind to the first event loop that touches them | UC2/UC3 checkpointer work (#58) | `adapters/engines/langgraph/checkpointer.py`'s docstring; restated in §5, point 4 |
| `httpx.ASGITransport` cannot prove live signal/response interleaving; that proof needs a real ASGI server | UC3 (#54) | `agentdeck-v2-architecture.md` §8's 2026-08-05 amendment; re-grounded for Story 3 in the epic amendment (§7) |
| `origin` is invocable-scoped, not sub-agent-scoped — **ruled Option B**, 2026-08-05 | UC1 (#52 review) | `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment; ruled in §3; stated in `core/events.py`'s `origin` docstring and `agentdeck-v2-architecture.md` §4.2 |

## 7. Doc amendments made in this PR

| File | Change |
|---|---|
| `milestone-0-walking-skeleton.md` §6 | Closing note: the go/no-go verdict, linking here and to the demo script |
| `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment | The ruling appended: invocable-scoped `origin` is the contract, not a gap |
| `epic-agentdeck-v2-core.md` | Dated amendment re-grounding Story 2's remaining scope and estimate in what the crude adapters taught |
| `agentdeck/core/events.py` | `origin`'s field docstring states the Option B contract — docstring only, no wire change |
| `agentdeck-v2-architecture.md` §4.2 | The same ruling, stated where the envelope is specified |
| `00-project-index.md` §4 | The "M0 finish" step marked done, pointing here and at `scripts/m0_demo.py` |

## 8. Keep / harden / discard — per skeleton component

Nothing keeps by default (milestone §6's rule).

| Component | Call | Why |
|---|---|---|
| `core/events.py` + `core/content.py` | **Keep** | The schema-freeze candidate (§4): zero falsifiers fired, divergences are strengthenings. Changes from here are dedicated schema PRs |
| `core/context.py`, `core/status.py`, `core/invocable.py`, `core/ports/*` | **Keep** | Same bar — Ring 1 nouns and ports, exercised by real traffic across two engines with no leaks |
| Contract suite (`tests/contract/`) | **Keep** | The "LSP made executable" artifact; it is what makes "add a third engine" checkable. Grows with Story 2/3, never replaced |
| `Runtime` (`agentdeck/runtime/service.py`) | **Harden** | The orchestration shape stays; error handling is spike-quality — bare `Exception` logging, no retry/backoff, no bounded sink-task set (`# ponytail` markers flag both). The abstraction is not up for debate |
| `adapters/engines/openai_agents/` | **Harden** | Proven on UC1 and UC3. Needs non-text `Input` blocks (images/resources raise `ConfigError` by design rather than dropping), the orphan-tool-call TODO in `translate.py` (#52), and non-`str` `final_output` |
| `adapters/engines/langgraph/` | **Harden** | Proven on UC2. Needs non-dict node state values (raises rather than misserializing — same posture), non-text `Input`, and the `TypedDict` contract from §5/§6 enforced by more than a docstring |
| `adapters/stores/memory`, `adapters/stores/sqlite` | **Harden** | `SessionStorePort` passed every durability and gap-recovery test; SQLite's one-connection-plus-lock posture needs revisiting (pooling, or accepting the ceiling) before real concurrent load. Redis/Postgres are net-new Story 2 work |
| `adapters/control/memory`, `adapters/control/sqlite` | **Harden** | Cancel-only scope proven end to end including cross-process. Story 3 extends `Signal` and must resolve the process-local resume lock (§5/§6) before a Redis-backed multi-worker `ControlPort` is safe |
| `surfaces/serve/app.py`, `surfaces/serve/workflows.py` | **Harden, and fold into the real composition root** | Proved "surfaces render events, never contain logic" across both engines with zero edits (§2). Still a parallel `/v2/...` route, not wired into `App`'s facade and not byte-parity with v1's `serve.py` |
| `surfaces/cli/chat.py` (the ~50-line renderer) | **Discard beyond its life as a reference consumer** | It proved `origin`+`message_id` sufficient for a real consumer and survived a truncated replay. Not the Phase-2 renderer: no delta buffering, `node.updated`/`custom` silently skipped, and an uncapped `result_preview` printed in full. Milestone §6 named this disposal |
| Hardcoded invocable registry (inline `dict[str, InvocableSpec]` per test/demo) | **Discard** | Named for discard from the start; Story 2 replaces it with the real `InvocableRegistry`. Nothing survives but the shape of what a `Mapping[str, InvocableSpec]` must provide |

## 9. Demo artifact

`python scripts/m0_demo.py` — deterministic and replayable, not a recording: UC1 → UC2 → UC3 against
real SQLite stores, real engines and the real `surfaces/serve` FastAPI apps, scripted fakes for every
model (no network, no API keys). It asserts every "make sure" item from milestone §2–§4, so a broken
skeleton fails with an `AssertionError` rather than a quietly nicer chat log; run 3× consecutively
during this review with an identical `PASS`. It lives outside the package, so it changes no production
code while still exercising a runnable composition root — the gap PR #59's review flagged.
