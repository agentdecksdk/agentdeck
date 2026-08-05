# Milestone 0 — Findings (the go/no-go checkpoint)

**Status:** accepted (falsifier review) · pending maintainer ruling (origin/speaker attribution)
**Date:** 2026-08-05 · **Relates to:** `milestone-0-walking-skeleton.md` §6, `agentdeck-v2-architecture.md`,
`adr-d5-two-stores.md`, `epic-agentdeck-v2-core.md`, `prompts/pr1-event-schema-prompt.md`
**Closes:** issue #57 (the M0 finish checkpoint)

This is the "at the finish" writeup `milestone-0-walking-skeleton.md` §6 asks for: the
falsifier review (go/no-go), the schema-as-built diff, the learning note, the decision
log, and the keep/harden/discard call — all in one place because they are one review, not
four. The demo artifact is `scripts/m0_demo.py` (§9).

---

## 1. Verdict

**GO.** None of the six falsifiers in milestone §6 fired. The epic proceeds to Story 2
hardening rather than pausing for redesign. One open design finding — whether `origin`
should be invocable-scoped or sub-agent-scoped — has an analysis in §3 with a
recommendation, but the ruling itself is **PENDING MAINTAINER DECISION**, per this
checkpoint's own instructions: this document does not decide it.

---

## 2. Falsifier review — evidence, not impression

| # | Falsifier | Verdict | Evidence |
|---|---|---|---|
| 1 | Any consumer needed to know which engine produced an event | **Did not fire** | `grep -rn "openai-agents\|langgraph\|OpenAIAgentsEngine\|LangGraphEngine" agentdeck/surfaces/` returns zero hits in any surface module (the one hit anywhere under `core/` is a docstring in `core/ports/engine.py`, not branching code). The `.importlinter` contract `surfaces-are-adapter-free` makes this structural, not just observed: `agentdeck.surfaces` is forbidden from importing `agents`, `langgraph`, `redis`, or `agentdeck.adapters` at all, so a surface could not branch on engine identity even if someone tried — `make lint-imports` enforces it in CI. `surfaces/serve/app.py::build_app` and `surfaces/cli/chat.py::render` are the exact same functions for UC1 (openai-agents) and UC2 (langgraph); `scripts/m0_demo.py` drives both through them. |
| 2 | The schema needed a new **required** field after UC1 events were already persisted | **Did not fire** | `git show 40761bd -- agentdeck/core/events.py` (PR #58, UC2/langgraph) and `git show edc6e4f -- agentdeck/core/events.py` (PR #59, UC3/control) both produce an empty diff. `agentdeck/core/events.py` has not changed since it was created in PR #56 (UC1, commit `35de20a`). |
| 3 | UC2 required editing any UC1 consumer | **Did not fire** | `git show 40761bd -- agentdeck/surfaces/serve/app.py agentdeck/surfaces/cli/chat.py` and `git show edc6e4f -- agentdeck/surfaces/serve/app.py agentdeck/surfaces/cli/chat.py` are both empty. UC2 added a new, additive file (`agentdeck/surfaces/serve/workflows.py`, `/pending`+`/resume`) instead of touching `app.py`; UC3's cancel wiring lives entirely in `Runtime._with_gate`, which rebinds `ctx.gate` before an engine ever sees it, so the chat route needed no changes either. `test_uc2_claim_pipeline.py` and `test_uc3_slowpoke.py` both import and run the unmodified `surfaces/serve/app.py::build_app` and `surfaces/cli/chat.py::render`. |
| 4 | Transcript fidelity was only achievable with byte-level normalization | **Did not fire** | `tests/test_uc1_handoff.py::_message_transcript_from_log` / `_message_transcript_from_session` compare with plain `==` on `list[tuple[str, str]]` — no `.strip()`, `.lower()`, whitespace collapsing, or regex substitution anywhere in the file (`grep -n "normalize\|\.strip()\|replace(" tests/test_uc1_handoff.py` is empty). `test_langgraph_transcript_fidelity` in `tests/test_uc2_claim_pipeline.py` compares the checkpointer's `aget_state().values` against the log's shallow-merged `node.updated` patches with plain dict `==`, same result. Both are exact-equality tests, run in `make check`. |
| 5 | Exactly-one-terminal-event needed consumer-side workarounds (timeouts, dedupe) | **Did not fire** | The invariant is enforced inside `Runtime.run`/`resume` (`agentdeck/runtime/service.py`): a terminal payload breaks the read loop ("Terminal means terminal: stop reading so nothing can follow it into the log"), and all four exit paths (terminal, suspend, exception, abandoned-consumer `GeneratorExit`) close the run in the log before returning. No consumer contains a timeout- or dedupe-based workaround: `grep -rn "timeout\|dedup" agentdeck/surfaces/ tests/test_uc1_handoff.py tests/test_uc2_claim_pipeline.py tests/test_uc3_slowpoke.py` only matches `subprocess.run(..., timeout=...)` calls (test-harness plumbing for a real OS process, not stream-processing logic). `check_terminal()` is used only as a test assertion, never by a runtime consumer. |
| 6 | The gap-detection test could not be made reliable | **Did not fire** | `test_uc3_chaos_gap_detection_recovers_from_store` drops a specific `seq` from an already-fully-materialized `list[Event]` in a plain synchronous generator (`lossy_stream()`) — the "loss" is deterministic by construction, not a timing race, so it cannot flake. Separately, the milestone's mandated 20×-loop (`test_uc3_cancel_lands_at_next_safe_point_stable_across_20_runs`) asserts `check_contiguous(events) == []` on every one of 20 trials; it has been run repeatedly in `make check` (§10) with no failures observed. |

---

## 3. The open design finding: is `origin` invocable-scoped or sub-agent-scoped?

**The gap, restated.** `core/events.py`'s `origin: str` field is exactly as specified —
"the invocable that produced it, never the engine." The shipped `Runtime._record` stamps
`origin = spec.name`, where `spec` is the top-level `InvocableSpec` the caller addressed
(`Runtime.run("FrontDesk", ...)`). After an internal openai-agents handoff, every event of
that run — including `ClaimsAgent`'s own messages and tool calls — still carries
`origin="FrontDesk"`. `message_id` still gives each message a distinct identity, so
nothing is lost or misattributed at the event level, but a consumer cannot use `origin`
alone to answer "which sub-agent said this." `tests/test_uc1_handoff.py` asserts the
shipped behavior on purpose (`assert all(line.startswith("FrontDesk [") ...)`) so this
prints loudly the day it changes, instead of silently.

This is **not a schema divergence** (§4) — the field's shape and the top-level "never the
engine" rule are both honored exactly. It is a **behavioral** choice about what value the
Runtime stamps, and it was never decided at design time; the design doc's own worked
example (§7) never runs a multi-agent handoff, so nobody noticed the ambiguity until UC1
exercised one for real.

### Option A — an engine-supplied speaker attribution, additive to a payload

Add an optional field (e.g. `speaker: str | None`) to the content-bearing payloads
(`TextDelta`, `ThoughtDelta`, `MessageCompleted`, and arguably `ToolCallStarted`/
`ToolCallCompleted`) carrying the sub-agent name the *engine* reports as having produced
that particular item — for openai-agents, the SDK's current agent context already tracks
this (`translate.py` would need to thread it through the same way it already threads
`tool_names`). This is additive under D8 (a new optional field, no `v` bump) and does not
touch the closed envelope (D9) — it goes in the payload, exactly where D9 says new needs
belong.

*Costs:* touches `core/events.py` (a schema PR, per coding-standards.md §7 — "new kinds
and envelope changes appear only in dedicated schema PRs" applies by the same logic to
new payload fields with cross-engine meaning) and both engine adapters' translation code;
`node.updated`'s existing `node` field already plays this role for langgraph, so the
langgraph adapter needs no new data, only a naming reconciliation with whatever field name
is chosen. Golden JSON snapshots for every affected payload change (declared, per
coding-standards.md §7). No breaking change to already-persisted events — old events
simply lack the new field, which parses fine under `extra="ignore"`.

### Option B — redefine "speaker" as the invocable, document the limit

Keep `origin` exactly as shipped and write down, in the schema's own docstring and in the
architecture doc, that "speaker" in this platform means *the invocable a caller
addressed*, not *the SDK's internal sub-agent*. A UI that wants finer attribution inside
one invocable's run builds it from `message_id` sequencing plus (for openai-agents)
replaying the namespaced `custom` handoff events (`openai_agents.handoff`,
`{from, to}`) it already gets — engine-specific, so a consumer that leans on this for
anything beyond decoration is knowingly trading the "never the engine" property for a
feature, and that trade is theirs to make, not the platform's to hide.

*Costs:* zero code change. The tradeoff is real: a `FrontDesk` → `ClaimsAgent` handoff
will keep rendering as one visually-labeled speaker across two engine sub-agents in
every UI built on the canonical stream, for as long as this stands. Nobody has asked for
finer attribution outside this spike's own "make sure" bullet, which was itself written
before a real handoff was run.

### Recommendation

Option B, **for now** — YAGNI: nobody outside this checkpoint's own falsifier list has
asked for sub-agent-level speaker attribution, Option A is real (if bounded) schema
surface to carry indefinitely, and Option B costs nothing today while leaving Option A
fully available later as a strictly additive change (no migration, no `v` bump) the
moment a concrete consumer needs it. If a UI story lands in the PRD backlog that requires
distinguishing sub-agents inside one run, Option A is the correct fix at that point, done
as its own dedicated schema PR.

**RULING: PENDING MAINTAINER DECISION.** This section presents the options; it does not
choose between them. The maintainer rules in PR review for issue #57, and the ruling gets
folded back into `milestone-0-walking-skeleton.md` §2's amendment and this section as a
follow-up dated note.

---

## 4. Schema-as-built diff

Diffed: `agentdeck/core/events.py` + `agentdeck/core/content.py` against design doc §4.2
and the nine review decisions (`00-project-index.md` §5: 1 nested envelope, 2
`UnknownEvent`, 3 contiguous Runtime-assigned `seq`, 4 `origin`, 5 `message_id`, 6 usage
per-call+aggregate, 7 preview+hash results, 8 structured `run.failed`, 9 dot-case naming;
plus decision A=contiguous `seq`, B=full-text `message.completed`).

**Result: every envelope field, every kind, and all nine decisions plus A/B match the
design doc and the PR #1 prompt exactly** — same eight envelope fields, same payload
catalog, same discriminators, same closed-envelope rule. The divergences below are all
implementation-detail strengthenings within that contract, not shape changes to the wire
format; each gets its one-line justification.

| Divergence | As designed / specified | As built | Justification |
|---|---|---|---|
| Model mutability | Not specified (design doc shows plain `BaseModel`s) | `CoreModel` (the shared base for every payload and content block) sets `frozen=True` | coding-standards.md §4: "mutating an event after construction is a bug by definition." Prevents an event or content block handed to multiple sinks from being mutated by one of them. |
| `extra` policy location | PR #1 prompt: "pick one, apply uniformly" (mechanism unspecified) | One shared `CoreModel(extra="ignore", frozen=True)` in `content.py`, inherited by every payload in `events.py` | DRY: one place implements and can be audited for the forward-compat contract, instead of repeating `model_config` per class. |
| `UnknownEvent`'s own `extra` policy | Not called out as a special case | `UnknownEvent` overrides to `extra="forbid"`, stricter than the shared default | Documented in its own docstring: it sits in the same discriminated union as the known payloads, so laxness there would let a malformed *known* payload silently validate as `UnknownEvent` instead of raising — the one place uniformity would have hidden a real bug. |
| Integer field strictness | `seq: int`, `result_size: int`, token counts: `int` | `seq`, `result_size`, `Usage.input_tokens`/`output_tokens`: `NonNegativeInt` | Matches the domain invariant (seq is contiguous *from 0*; sizes and token counts cannot be negative) at the type level. No wire-format change — a JSON integer either way. |
| `parse_event`'s unknown-kind detection | "on an unknown `kind`, returns an Event whose payload is `UnknownEvent`" | Also accepts an *already-wrapped* `{kind, raw_payload}` shape (ambiguous with a stored `UnknownEvent`) as itself an `UnknownEvent` | Required for round-trip: a `store.append()`'d `UnknownEvent` must survive `model_dump_json` → `parse_event` → equality, which the prompt's literal wording (parse a *raw* unknown-kind dict) doesn't by itself cover. Tested in `tests/core/test_events.py`. |
| `Budget`'s naming | PR #1 prompt writes `RunContextSnapshot.budget` as an inline anonymous shape (`{max_usd, max_tokens} \| None`) | A named `Budget` class in `core/events.py`, reused by `RunContextSnapshot` | Matches the design doc's own §4.3 usage (`Budget(max_usd=2)`) and avoids two anonymous-but-identical shapes; same wire format either way. |

**Not a divergence, cross-referenced instead of repeated here:** the `origin` field's
*shape* is exactly as specified; the open question is which *value* the Runtime stamps
into it for a multi-agent run, covered in full in §3.

---

## 5. What the skeleton taught, ranked by "expensive to learn in Phase 2"

1. **The two-store rule (ADR-D5) needed a real multi-turn handoff to prove, and it held.**
   Before UC1 ran, "the SDK session feeds the model, the log is the record" was a design
   argument backed by a worked *example*, not a worked *test*. Learning after Story 2 was
   fully built that the log-only reconstruction path silently degrades multi-turn context
   (the exact failure ADR-D5 predicts) would have meant rewriting Story 2's core
   abstraction after every consumer had already been built on top of it. UC1 proved the
   invariant empirically (`test_uc1_handoff.py`'s transcript-fidelity assertion) at spike
   cost instead.
2. **`EnginePort`/`Runtime` genuinely covers two structurally different execution models
   with zero consumer-visible difference.** This was the epic's single highest-risk bet
   (Story 2's own "Risk: highest of the epic" note). It is now a *measured* fact, not a
   hope: `surfaces/serve/app.py` and `surfaces/cli/chat.py` are byte-identical across UC1
   and UC2, verified by `git show` producing empty diffs (§2, falsifiers 1 and 3).
3. **LangGraph's bare-`dict` state-schema trap would have silently broken
   `node.updated`'s documented contract.** A `StateGraph(dict)` treats the whole state as
   one opaque channel, so a node's return *replaces* state instead of merging into it —
   discovered only because a UC2 fixture used a bare `dict` and the shallow-merge
   assertion failed. `node.updated`'s docstring says "shallow-merge"; nothing enforced it
   until this was found. Recorded as its own point in §6 because it needed a documented
   contract (every `StateGraph` in this codebase must use a `TypedDict` or pydantic
   model), not just a fixture fix.
4. **A durable LangGraph checkpointer binds to the event loop that first constructed it.**
   `checkpointer.py`'s `_sqlite_saver`/`_postgres_saver` are `@cache`d per URL and hold an
   internal lock bound to whichever loop first acquired it — fine for one long-lived loop
   per process (a server), a real constraint for any script or test harness calling
   `asyncio.run()` more than once against the same durable graph. Cheap to learn now (a
   documented note in the module); expensive to learn as a mystifying "Lock … bound to a
   different event loop" production incident later.
5. **`httpx.ASGITransport` cannot interleave a live signal into an in-flight SSE
   response.** It runs a request's whole ASGI call before returning any bytes, which is
   why UC3's chat-route cancellability is proven by architecture (`Runtime._with_gate`,
   exercised by every other control test) rather than a dedicated HTTP-level test. This
   matters directly for Story 3's acceptance criteria, which will need a real ASGI server
   (e.g. `uvicorn` in a subprocess, not `ASGITransport`) to test pause/cancel over a live
   HTTP stream — cheap to flag now in the epic amendment (§7), expensive to discover as a
   mysteriously-untestable acceptance criterion mid-Story-3.
6. **The double-resume guard is process-local, and that boundary is easy to miss.** It is
   an `asyncio.Lock` keyed by `run_id` inside one `Runtime` instance — correct for two
   callers racing one process, silently wrong for two processes racing the same run
   through two separate `Runtime`s over the same store, which is exactly the shape a
   multi-worker deployment has. A store-level CAS primitive is needed before Redis-backed
   multi-worker resume is safe; recorded in `milestone-0-walking-skeleton.md` §3's own
   2026-08-05 amendment, restated in §7 below for the epic.

---

## 6. Decision log — ad hoc calls promoted to documented contract

| Decision | Made ad hoc during | Where it now lives |
|---|---|---|
| Interrupt-first safe-point contract: an interrupting node calls `interrupt()` as its first statement, so re-entry on resume repeats nothing | UC2 (#53) | `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment |
| Double-resume guard is process-local (`asyncio.Lock` per `run_id`, not a store-level CAS) | UC2 (#53) | `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment; re-grounded for Story 3 in the epic amendment (§7) |
| `Runtime._with_gate` rebinds `ctx.gate` only when built with a `ControlPort`, so UC1's consumers never learn control exists | UC3 (#54) | `agentdeck-v2-architecture.md` §8's 2026-08-05 amendment |
| `StateGraph` schemas in this codebase must be `TypedDict`/pydantic, never a bare `dict` | UC2 fixture debugging (#58) | This document (§5, point 3) — promoted here; a lint or runtime check is future work, not done in this PR |
| Durable checkpointers are cached per URL and bind to the first event loop that touches them | UC2/UC3 checkpointer work (#58) | `agentdeck/adapters/engines/langgraph/checkpointer.py`'s own docstring; restated here (§5, point 4) for visibility outside that file |
| `httpx.ASGITransport` cannot prove live signal/response interleaving; that proof needs a real ASGI server | UC3 (#54) | `agentdeck-v2-architecture.md` §8's 2026-08-05 amendment; re-grounded for Story 3 in the epic amendment (§7) |
| `origin` is invocable-scoped, not sub-agent-scoped | UC1 (#52 review) | `milestone-0-walking-skeleton.md` §2's 2026-08-05 amendment; analyzed and left open in §3 above |

---

## 7. Doc amendments made in this PR

- `milestone-0-walking-skeleton.md` §6: closing note recording the go/no-go verdict and
  linking here and to the demo script.
- `epic-agentdeck-v2-core.md`: dated amendment re-grounding Story 2's remaining scope and
  estimate in what the crude adapters actually taught (see the amendment itself for detail
  — summarized in §5-6 above).
- `00-project-index.md` §4: the "M0 finish" execution-order step is marked done, pointing
  at this document and `scripts/m0_demo.py`.
- This document is new.

---

## 8. Keep / harden / discard — per skeleton component

Nothing keeps by default (milestone §6's own rule). Each call below is a decision, not a
default.

| Component | Call | Why |
|---|---|---|
| `core/events.py` + `core/content.py` (schema) | **Keep** | Milestone §6's schema-freeze candidate (§4 above): zero falsifiers fired, divergences are all strengthenings. This is the frozen v1 schema; changes from here are dedicated schema PRs. |
| `core/context.py`, `core/status.py`, `core/invocable.py`, `core/ports/*` | **Keep** | Same bar as the schema — these are Ring 1 nouns and ports the whole design rests on, exercised by real traffic across two engines with no leaks found. |
| Contract suite (`tests/contract/`) | **Keep** | Exactly the "LSP made executable" artifact the design doc calls for; it is what makes "add a third engine" a checkable claim, not a hope. Grows with Story 2/3, never replaced. |
| `Runtime` (`agentdeck/runtime/service.py`) | **Harden** | The orchestration shape (stamp/append/fan-out/yield, four-exit run-closing, resume-lock) is right and stays, but error handling is spike-quality: bare `Exception` logging with a type name, no retry/backoff policy, no bounded sink-task set (`# ponytail` markers already flag both). Story 2 hardens in place; the abstraction is not up for debate. |
| `adapters/engines/openai_agents/` | **Harden** | Proven against a real multi-turn, multi-agent, tool-calling conversation (UC1) and a cancel-under-load conversation (UC3). Needs: non-text `Input` blocks (images/resources currently raise `ConfigError` by design, not silently drop — a real gap, not a bug), the orphan-tool-call TODO in `translate.py` (`#52`), non-`str` `final_output` support. |
| `adapters/engines/langgraph/` | **Harden** | Proven against interrupt/resume/restart (UC2). Needs: non-dict node state values (currently raises rather than misserializing — same "raise, don't drop" posture as openai-agents), non-text `Input`, and the `TypedDict`-schema contract from §5/§6 enforced by more than a docstring. |
| `adapters/stores/memory`, `adapters/stores/sqlite` | **Harden** | Contract (`SessionStorePort`) is right and passed every UC's durability and gap-recovery test; the SQLite adapter's one-connection-plus-lock posture is fine for M0's scale and needs revisiting (pooling, or accepting the ceiling) before real concurrent load. Redis/Postgres stores are net-new work for Story 2, not a hardening of what exists. |
| `adapters/control/memory`, `adapters/control/sqlite` (`ControlPort`) | **Harden** | Cancel-only scope proven end to end, including cross-process (UC3). Story 3 extends `Signal` (pause/resume/steering) and must resolve the process-local resume-lock limitation (§5/§6) before a Redis-backed multi-worker `ControlPort` is safe to ship. |
| `surfaces/serve/app.py`, `surfaces/serve/workflows.py` (crude SSE + `/pending`/`/resume`) | **Harden, and fold into the real composition root** | Proved the "surfaces render events, never contain logic" property across both engines with zero edits between UC1 and UC2/UC3 (§2). Not yet the platform surface: it is a parallel `/v2/...` route, not wired into `App`'s compat facade, and not byte-parity with v1's `serve.py`. Story 2 is where it becomes that. |
| `surfaces/cli/chat.py` (the ~50-line renderer) | **Discard beyond its life as a reference consumer** | It did its one job — prove the envelope's `origin`+`message_id` fields are sufficient for a real consumer to distinguish speakers and bubbles, and survive a truncated replay without crashing (UC3). It is not the CLI renderer Phase 2 ships: no delta-buffering for a smoother terminal UX, no handling for `node.updated`/`custom` beyond silently skipping them, and it prints an uncapped `result_preview` line in full (visibly, in `scripts/m0_demo.py`'s own transcript) rather than truncating for display. Milestone §6 named this disposal explicitly; this confirms it. |
| Hardcoded invocable registry (`dict[str, InvocableSpec]` built inline, per test/demo) | **Discard** | Named for discard in milestone §6 from the start. Story 2 replaces it with the real `InvocableRegistry` (today's `PluginRegistry`/`runtime/discovery.py` convention, per the design doc's migration map) — nothing here survives except the shape of what a `Mapping[str, InvocableSpec]` needs to provide. |

---

## 9. Demo artifact

`scripts/m0_demo.py` — a deterministic, replayable script (not a recording) that runs
UC1 → UC2 → UC3 end to end against real SQLite-backed stores, real engines, and the real
`surfaces/serve` FastAPI apps, with scripted fakes for every model (no network, no API
keys). Run it with:

```bash
python scripts/m0_demo.py
```

It prints a narrated transcript of all three use cases and asserts every "make sure" item
from milestone §2–§4 that the automated test suite also covers (this script is those
tests' human-watchable replay, not a separate claim); a broken skeleton fails it loudly
with an `AssertionError`, not a quietly-nicer chat log. It was run 3× consecutively during
this review with an identical `PASS` outcome each time (§2, falsifier 6's evidence for
determinism applies here too). It lives under `scripts/`, not inside the `agentdeck`
package, so it changes zero lines of production code while still exercising a real,
runnable composition root — the gap PR #59's review flagged (`surfaces/serve`'s
`build_app` was previously only ever called from tests).
