# 00  -  AgentDeck Project Index

**The one file to read first.** It maps every document, says which one wins when they disagree,
reconciles the known deltas, and gives the execution order.
Date: 2026-08-04. Amended 2026-08-11, 2026-08-14.

> **Amendment 2026-08-11  -  v3 is built.** The title drops its version, because this file outlives
> any one release. Two corrections: §4's `NOW` arrow pointed at a cutover that has since finished,
> and §1 listed only the v2-era set, which the v3 cutover roughly doubled (§1b). §2 and §3 are
> unchanged. The v2/v3 naming in the *design* documents stays  -  `agentdeck-v2-architecture.md` and
> `adr-d5-two-stores.md` describe the layout and import law that shipped as v3, and renaming files
> to chase a version bump is churn.
>
> **Amendment 2026-08-14  -  the writing standard.** Every document under `docs/` was swept against
> it: the main doc is the map, depth links out, per-item facts are tables, one sentence per ruling.
> No document was deleted. Three files were split out of the architecture doc, which fell from
> 1536 lines to 1408: `design/run-lifecycle.md` (§4.4/§4.5), `design/event-store-claims.md` (§4.5)
> and `design/sink-dispatch.md` (§4.6)  -  docs #12–#14, §2 rule 4. Three delivery documents §6 had
> never listed gained rows in §1b.

---

## 1. The document set

| # | File | What it is | Audience |
|---|------|------------|----------|
| 1 | `project-brief.md` | One-page what/why/scope/risks | anyone, 3 minutes |
| 2 | `agentdeck-prd.md` | Product requirements FR-1…25, personas, release phasing | product view |
| 3 | `design/agentdeck-v2-architecture.md` | The design: three rings, core nouns, ports, worked examples, SOLID scorecard, migration map. Appendix A: stdlib. Appendix B: user's-view examples (load-bearing) | engineers |
| 4 | `design/adr-d5-two-stores.md` | Decision record: event log vs engine execution state. **Supersedes** D5 in doc #3 | engineers |
| 5 | `delivery/epic-agentdeck-v2-core.md` | Delivery plan: 1 epic, 5 stories, acceptance criteria, dependency graph | delivery |
| 6 | `delivery/milestone-0-walking-skeleton.md` | Pre-epic validation spike: 3 adversarial use cases, per-step gates, falsifier checklist | delivery |
| 7 | `prompts/pr0-baseline-prompt.md` / `prompts/pr0-review-prompt.md` | Executable handoff: author + reviewer prompts for the safety-net PR | coding agent |
| 8 | `prompts/pr1-event-schema-prompt.md` | Executable handoff: the frozen event schema spec as an author prompt. **Most current schema statement** | coding agent |
| 9 | `delivery/docs-site-plan.md` | External docs site (`docs-site/`): IA, content rules, anti-rot tests, phases DS-0…DS-4 | delivery, docs |
| 10 | `delivery/milestone-0-findings.md` | M0's go/no-go checkpoint: falsifier review, schema-as-built diff, learning note, decision log, keep/harden/discard | delivery, engineers |
| 11 | `design/adr-d11-store-assigns-seq-and-time.md` | Decision record: the store assigns `seq` and `ts` in the same atomic step that persists an event. **Supersedes** `claim_start`'s "a store never reads a clock" and the envelope-stamping split in doc #3 | engineers |
| 12 | `design/run-lifecycle.md` | The run lifecycle as built: the state machine, per-state properties, the (state × intent) policy, and the drift. **Supersedes** doc #3 §4.4 | engineers |
| 13 | `design/event-store-claims.md` | `claim_start` and `claim_resume`: the two conditional appends, the session-busy rule, the staleness window and its operator consequences. **Supersedes** doc #3 §4.5 on the claims | engineers |
| 14 | `design/sink-dispatch.md` | `runtime/dispatch.py`'s operational contract: bounded fan-out, the breaker, the flush/close lifecycle. **Supersedes** doc #3 §4.6 on the dispatch | engineers |
| 15 | `design/run-operations.md` | The surface that acts on an existing run: the `deck.runs.*` namespace, its eight ops, and the internal deadline sweep that replaces public `tick`/`due`. **Amends** `delivery/decision-v3-entry-point.md` ruling 2 and its no-daemon note. Semantics stay in doc #12. **Its "there is no per-run object" ruling is superseded by doc #16**; the deadline sweep is not | engineers |
| 16 | `design/run-identity.md` | Run identity (`id`, `namespace`, `key`), the `Run` object, session ownership, execution ownership, and the control plane's cross-tenant defect. **Supersedes** doc #15's surface ruling: lifecycle ops move onto `Run` and `deck.runs` keeps `start`/`get`/`list`. Semantics stay in doc #12 | engineers |
| 17 | `design/execution-api.md` | The v5 execution API: `ctx`, `Run`, `run.can.*`, `Reporter`, and the one `Executor.execute()` that any target becomes a Run through. Rules #336 and #337. Lifecycle semantics stay in doc #12, identity in doc #16 | engineers |

## 1b. The v3 cutover set (added 2026-08-11)

Everything written after the table above, in the order the work happened. §6's rule  -  one row here
per new spec doc  -  had fallen behind by thirteen documents (2026-08-11) and three more (2026-08-14).

| File | What it is |
|---|---|
| `coding-standards.md` | **The standards of record**: typing, errors, the async/event-path law, test structure, naming, dependencies, binary assets, security, PR/commit discipline. `CLAUDE.md` points every agent here before any non-trivial code |
| `delivery/plan-v2-cutover.md` | The cutover plan: phases 0–4, and ruling 1 (v1's public API is dropped, not facaded)  -  the decision that renumbered the release train |
| `delivery/decision-v3-entry-point.md` | The entry-point brief that produced `Deck`: options weighed, `App` retired, two front doors and one catalog |
| `delivery/plan-phase4-deck.md` / `review-phase4-deck.md` | Phase 4's build plan and its review round |
| `delivery/plan-skills.md` | Skills as `SKILL.md` prose plus `key=value` stdout, and why an agent never imports a skill's schema |
| `delivery/plan-multimodal.md` | The content model end to end  -  the full block set, per-engine reach, inline caps  -  gating `AudioBlock`/`ImageBlock` |
| `delivery/plan-context-injection.md` | The original `Context[T]` design |
| `delivery/plan-166-delivery.md` / `review-context-injection.md` | What moved under that design once it met both engines, sliced for delivery; and the review |
| `delivery/roadmap-v3.md` | Every open v3.0.0 issue assessed against the tree, the rulings taken, and the waves. **Delivered**  -  see its closing note |
| `delivery/plan-219-delivery.md` | Jack (#219)  -  the reference application as release-level validation of the frozen v3 surface: six rulings, four slices, and the friction ledger that is its real deliverable |
| `delivery/roadmap-v3.1.md` | **What's next.** Every open issue assessed against the tree after v3.0.0, the rulings needed, and the backlog sorted into v3.1 / v3.2 / v3.3  -  including the reference app's ledger findings, filed as issues |
| `delivery/findings-register.md` | **Every finding and its disposition**  -  solved, rejected, or scheduled against a named issue. The disposition of record for anything raised in conversation that never became an issue, and the reason #221 was caught closed-but-unfixed |
| `delivery/beta-user-report-v3.md` | A real `v3.0.0b1` run by a first-time user; source of the Wave B findings |
| `delivery/deck-capability-wrapper-pattern.md` | The wrapper shape a `Deck` argument takes when a capability needs one |
| `delivery/workflow.md` | How an issue/finding/PR moves through the [GitHub Project](https://github.com/users/sagi5060/projects/5): filing conventions, the `Status` pipeline, `make roadmap-sync` |
| `delivery/review-v3-outsider.md` | The clean-room outsider review of `v3.0.0`, united across three reviewers  -  the source of findings #229–#255 |
| `delivery/plan-adoption.md` | Discoverability and adoption: the domain cutover, machine discovery, searchable content, the contributor loop |
| `delivery/discoverability-baseline.md` | The 2026-08-12 zero point  -  30 questions across Context7 and GitHub search, and how to re-run them |

**Reading orders.** New engineer: `coding-standards.md` → 1 → 3 (through §9) → 4 → 8 → 12 →
`delivery/decision-v3-entry-point.md`. Product/stakeholder: 1 → 2 → 3 Appendix B. Historical:
7 → 8 → 6 → 10 → 5.

## 2. Precedence  -  which document wins

Documents were written in conversation order and later ones refine earlier ones. Rule:
**the more specific and more recent artifact wins**, concretely:

1. `prompts/pr1-event-schema-prompt.md` **is** the event schema. Where design doc §4.2 differs
   (it predates several decisions), PR #1 wins.
2. `design/adr-d5-two-stores.md` **is** the session-state rule. Design doc §5/§11/§12-D5 read
   through the ADR's §5 amendment list until edited.
3. `design/adr-d11-store-assigns-seq-and-time.md` **is** the rule for who assigns `seq` and `ts`,
   and it outranks rule 1 on that one question. It supersedes coding-standards §6
   (`docs/coding-standards.md:113`), ADR-D5's *Explicitly unchanged* clause (`:151`, "`Runtime`
   still stamps and appends every event"), `prompts/pr1-event-schema-prompt.md:34` and `:121`
   (frozen as history, so superseded here rather than edited), and the design doc's
   envelope-stamping split. **ADR-D5's two-store rule is untouched**, as is the engine boundary  -
   engines yield payloads, never envelopes.
4. Three files hold depth split out of the design doc on 2026-08-14, one subject each, each
   summarised and linked from the section it came from; where a summary and its file differ, the
   file wins. `design/run-lifecycle.md` (from §4.4) **is** the run lifecycle,
   `design/event-store-claims.md` (from §4.5) **is** the two conditional appends  -  subject to rule
   3, which owns who assigns `seq`  -  and `design/sink-dispatch.md` (from §4.6) **is** the sink
   dispatch contract.
5. `delivery/milestone-0-walking-skeleton.md` reorders early delivery: the epic's Phase 1/2 now
   execute *through* the skeleton (see §4 below). Epic story content is unchanged;
   sequencing defers to this index.
6. The PRD owns *what and for whom*; the design doc owns *how*; neither restates the
   other. A conflict between them is a bug in one of them  -  flag it, don't guess.
7. **The [GitHub Project](https://github.com/users/sagi5060/projects/5) owns live state**
   (which issues are open, their milestone, priority and `Status`); `roadmap-v3.md` /
   `roadmap-v3.1.md` / `findings-register.md` own the reasoning behind that state and do not
   self-update. Their live-status tables are generated  -  `make roadmap-sync`  -  and GitHub wins
   if the two disagree. See `delivery/workflow.md`.

## 3. Known deltas (recorded so nothing is silently inconsistent)

| Where | Delta | Resolution |
|---|---|---|
| Design doc §4.2 envelope & kinds | Predated `origin`, nested payload, `UnknownEvent`/`parse_event`, D9, D10, decisions A/B, `input.appended`, `run.started` join point | **Applied 2026-08-04**  -  §4.2 rewritten as a summary of the PR #1 schema (which remains authoritative) |
| Design doc D5 + §5 + §7 example + §11 migration row | Superseded by ADR-D5 (two stores; `SessionFactory` moves into the openai-agents adapter) | **Applied 2026-08-04**  -  all five passages amended; D9/D10 added to §12 |
| Epic Story 2 | Scope sentence re-homed `sessions.py` into `adapters/stores/` (pre-ADR); ADR-required tests absent *(note: an earlier version of this row misdescribed the delta as an obsolete acceptance criterion  -  no such AC existed)* | **Applied 2026-08-04**  -  scope corrected; transcript-fidelity + crash-reconciliation ACs added |
| Epic Story 3 | Steering (Story 3b: mailbox gate, `POST /runs/{id}/messages`) decided after the epic was written | **Applied 2026-08-04**  -  Story 3b added to Story 3 scope, same release |
| Milestone 0 §3 (UC2) | Two seq checks decided later: seq continuity across the kill/restart; double-resume race → exactly one winner | **Applied 2026-08-04**  -  added to UC2's make-sure list |
| Milestone 0 header | "A=contiguous, B=full-text  -  pending confirmation" | **Applied 2026-08-04**  -  confirmed; UC1/UC3 still test them empirically |
| PRD FR-17–21 (group sessions, moderator, advisors, triggers) | Designed in conversation, not yet in the architecture doc | Open  -  each gets a feature spec doc at its epic (v2.1/v2.2); architecture impact already assessed: zero new kinds, one new port (`TriggerPort`), one new component (Moderator as Invocable) |
| coding-standards §6 (`:113`) | Read "the Runtime is the **only** assigner of `seq`"  -  superseded by ADR-D11 | **Applied 2026-08-08**  -  §6 now states the store's assignment as the law |
| coding-standards §1 precedence | Enumerates "D1–D10 and ADR-D5"; by its own ordering D11 outranks nothing | **Applied 2026-08-08**  -  D11 named |
| ADR-D5 `:151` *Explicitly unchanged* | "`Runtime` still stamps and appends every event"  -  the exact sentence D11 overturns | **Applied 2026-08-08**  -  dated amendment added; D5's two-store rule untouched |
| `prompts/pr1-event-schema-prompt.md:34,121` | "assigned by the Runtime" / "seq is assigned only by the Runtime" | Superseded by precedence rule 3 above. Prompts are frozen (§6 below), so **not edited** |
| `core/ports/store.py` docstrings, `runtime/service.py:5,536-538`, `test_runtime_service.py:890` | All asserted or pinned Runtime-assigned `seq` | **Applied 2026-08-08**  -  port and `_drain` docstrings rewritten; the gap assertion flips `[2]` → `[]` |
| Design doc envelope-stamping split | Predated ADR-D11 | **Applied 2026-08-08**  -  dated amendment added beside it; the envelope line in §4.2 names the store |
| Design doc §4.4 | Named one guard point, an unreachable `CANCELLED` edge, and no (state × intent) policy | **Applied 2026-08-14**  -  §4.4 keeps the headline and the drift list; the depth is `design/run-lifecycle.md` (§2 rule 4) |
| `coding-standards.md` header | Called itself "doc #9 in `00-project-index.md`"; #9 is `delivery/docs-site-plan.md` | **Applied 2026-08-14**  -  the header names §1b instead |
| Design doc header | Read `**Status:** proposal`, while §2 and `CLAUDE.md` both treat it as the design of record | **Applied 2026-08-14**  -  dated amendment beside it; the status now says so |
| Design doc §4.5 `last_seq` | Named a port method ADR-D11 removed | **Applied 2026-08-14**  -  said so in place, in the amendment that introduced it |
| Design doc §4.5/§4.6 depth | The store-claim and sink-dispatch material had outgrown the sections holding it | **Applied 2026-08-14**  -  split to docs #13 and #14 (§2 rule 4) |

## 4. Execution order (single source of truth for "what's next")

```text
    PR #0  safety net: golden SSE baselines + import-linter          [prompt: doc 7]
    PR #1  event schema v1 = Skeleton Step 1                         [prompt: doc 8]
    M0 Steps 2–5: Runtime+stub → openai-agents+UC1 →
        langgraph+UC2 → control+UC3                                  [gates: doc 6 §5]
    M0 finish: demo script · falsifier review (GO) ·
        schema-as-built diff · findings note · keep/harden/discard    [doc 6 §6  -
        DONE, `delivery/milestone-0-findings.md`, `scripts/m0_demo.py`, #57]
    Epic Story 2 (the seam, full quality  -  re-sequenced per the
        findings note) → v2.0.0 tagged 2026-08-06
    v3.0.0 the cutover: phases 0–4 done, `App` and the v1 API
        deleted (#164), `Deck` the one composition root
        → v3.0.0b1 tagged 2026-08-10                                  [#88  -
        plan: `delivery/plan-v2-cutover.md`, brief:
        `delivery/decision-v3-entry-point.md`]
    Waves 1–5 on top of the beta: correctness, the wire, the config
        surface, `Context[T]`, observability, cleanup                 [all
        delivered  -  `delivery/roadmap-v3.md`]
    the pre-stable gate: the reference application (#219); #131 folded
        into it → **v3.0.0 tagged 2026-08-11**, docs site deployed,
        Jack live                                            [plan:
        `delivery/plan-219-delivery.md`]
NOW ──▶ v3.1 hardening → v3.2 batteries → v3.3 rooms & reach       [PRD §6  -
        the backlog sorted into those three: `delivery/roadmap-v3.1.md`,
        live per-issue status: the GitHub Project (`make roadmap-sync`)]
```

**Amendment 2026-08-08.** The epic planned v2.1 next; `plan-v2-cutover.md` ruling 1 (v1's public
API is dropped, not facaded) makes the next release breaking, so the batteries train renumbers
behind it  -  v2.1 → v3.1, and so on, contents unchanged. GitHub milestones mirror it: `v3.0.0  -  one
way to work`, `v3.1  -  batteries` (additive on the frozen API), `docs-site` (parallel, never
release-blocking).

Milestone 0 *is* Phase 1 plus a crude Phase 2/3 slice: after go/no-go, epic Story 2 hardens the
skeleton's adapters and Runtime rather than starting fresh (M0's keep/harden/discard decision).

## 5. Decision log (index of numbered decisions across the set)

| Decision | Where it is stated |
|---|---|
| **D1–D8**  -  engine boundary, no DSL, content blocks, caller-injected capabilities, two-store rule *(as revised by ADR-D5)*, cooperative cancel, ctx everywhere, event versioning | design doc §12 |
| **D9**  -  the envelope is closed (8 fields); new needs go in payloads or `run.started` | PR #1 prompt |
| **D10**  -  kinds are minted only in core; engines translate or use namespaced `custom`, and a recurring `custom` is a promotion signal. *Fired once, 2026-08-06, #101: two engines routing structured data around the schema promoted `DataBlock` into `core/content.py`  -  design doc §4.1/§4.2, additive under D8* | PR #1 prompt |
| **Schema review decisions 1–9 + A + B**  -  nested envelope, `UnknownEvent`, contiguous Runtime-assigned seq, `origin`, `message_id`, usage per-call + aggregate, preview + hash results, structured `run.failed`, naming; A=contiguous, B=full text | PR #1 prompt |
| **Standing refusals**  -  changing the list requires design review | PRD §8 / design doc §12 |

## 6. Housekeeping rules for the doc set

One statement of each fact: requirements live in the PRD, mechanisms in the design doc, sequencing
here. When implementation diverges from a doc, the doc gets a dated amendment in the same PR. Every
new feature epic opens with its own spec doc and one row added to §1 or §1b and to §4. Prompts
(docs 7–8) are frozen once their PR merges  -  history, not living docs, so they are never rewritten
even when a later ruling supersedes them (§2 rule 3 is how that is recorded instead).

Writing: `~/.claude/CLAUDE.md` §Writing is binding for every document here. A section that wants to
grow links out to a file of its own; per-item facts are a table; a ruling is one sentence.
