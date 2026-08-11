# Roadmap — after v3.0.0

**Status:** proposed · **Date:** 2026-08-11 · **Baseline:** `dev` at `ca03ae8`, v3.0.0 tagged and
released, docs site deployed, the reference application answering on `ask.agentdecksdk.com`.

Thirty-five open issues, assessed against the tree as it stands *after* the v3.0.0 tag, and
sequenced. Many were written against v1 or v2 and name classes that no longer exist; those are
called out rather than silently carried, exactly as `roadmap-v3.md` did before them.

**Thirty-five is a backlog, not a release.** The largest question this document asks is not what
to build first but what belongs in v3.1 at all — §4 proposes a split across the PRD's own
phasing, and it needs a ruling before anything is picked up.

## 0. Where the inputs came from

Two sources, and the second is new.

**The issue tracker**, which carries everything filed before and during v3.

**The v3 reference application's friction ledger** (`plan-219-delivery.md` §4). #219 was
release-level validation of the frozen surface, and building a real application against it
produced findings that no amount of reading would have. Three had never been filed and now are:
**#226** (`DataBlock` refused on input), **#227** (`asgi()` cannot serve a context-using app),
**#228** (a bundle cannot share a type with its host). A fourth, **#223**, came from the
grounding eval rather than from a person.

That closes the "extract relevant findings" thread the v3 pre-release plan opened: the findings
are issues now, and they are sequenced below with everything else rather than living in a report
nobody re-reads.

## 1. Relevancy pass

Verified against `dev` at `ca03ae8` by reading the tree, not the issue text.

### Premise dead — recommend closing

| # | What it says | What is true now |
|---|---|---|
| **#28** | stream nested deltas from `spawn_subagent` | **`spawn_subagent` does not exist in v3.** Nothing in `agentdeck/` mentions it. There is no feature to extend; if nested subagent streaming is wanted it is a new issue against `as_tool()`, written from scratch |
| **#43** | agent runs cannot be paused, resumed, cancelled or steered | Three of the four shipped. `Deck.pause/resume/cancel`, `SafePoint = Literal["stream_item", "tool_dispatch", "node_boundary"]`, and the control-event pair are all live. The remainder is steering (#46) and the workflow gap (#128), both of which have their own issues |
| **#44** | no public active-run lifecycle or control events | **Delivered.** `ControlRequested` and `ControlObserved` are in `core/events.py`, the second carrying `safe_point`. This is the contract the issue asked for |

### Concern valid, premise stale — text needs correcting before pickup

| # | Names something deleted | The live question |
|---|---|---|
| **#24** | `BaseWorkflow.pending()` draining `alist(None)` | `Deck.pending()` reads the **event log**, not the checkpointer, so the O(all checkpoints) claim is dead. Two remnants survive: parallel interrupts in one superstep, and the checkpointer scan that `due_resumes()`/`tick()` still do — which is #212's territory. Rescope or fold |
| **#35** | `BaseAgent.mcp_server_names` | `mcp=` on `Agent` is the surface now. No `tool_filter` anywhere in the tree, so the gap is real and unchanged |
| **#37** | `App.load()` | `Deck.from_project()`. The installer question is untouched, and now interacts with **#228** — both are about what may live under `.agentdeck/` |
| **#38** | `BaseAgent.tools` | `Agent(tools=[...])`. Hosted-tool presets still absent |
| **#20** | skills as pre-wrapped tools | **Half delivered.** `load_skill` gives progressive disclosure exactly as asked. The other half — the agent *executing* the bundle's scripts — is not there, and its scaffolding was deliberately deleted in #71. That half is a sandboxing question (#163), not a skills question |

### Valid as written

**#25**, **#26**, **#27**, **#34**, **#36**, **#46**, **#128**, **#129**, **#131**, **#133**,
**#135**, **#140**, **#167**, **#177**, **#178**, **#211**, **#212**, **#213**, **#217**,
**#218**, **#223**–**#228**.

Nothing else is stale enough to close.

## 2. What this session's evidence says about priority

Three issues were re-weighted by building the reference application, and the evidence is
concrete rather than a hunch.

**#26 — `agentdeck.testing`, the exported stub-runner harness. Strongest early candidate.** The
repo now hand-rolls a scripted model **three times**: `tests/scripted_model.py`,
`tests/test_docs_examples.py`, and `tests/test_ask_agentdeck_server.py`. The third exists because
the second could not script a tool call, which is the exact duplication #26 predicted in 2026-06.
Every issue below that needs a test against a model gets cheaper once this lands, which is why it
sorts early despite being unglamorous.

**#25 — auth on the serve surface. Newly evidenced, and the shape is now known.** Ask AgentDeck
is a public unauthenticated endpoint, and it hand-rolled an origin check, a per-client quota and
length caps because the framework offers none. Those are not exotic requirements; the reference
app is the specification. Whether they belong *in* the framework or stay an application concern
is a ruling, but the question is no longer abstract.

**#178 — handoffs fail against a non-OpenAI `OPENAI_BASE_URL`. No longer theoretical.** The
deployed reference application runs Gemini through the OpenAI-compatible endpoint — precisely the
configuration where a handoff returns a bare 400. AgentDeck's own live deployment sits on the
bug.

## 3. Rulings needed before anything starts

Each with a recommendation, because a bare question is not a proposal.

| # | Ruling | Recommendation |
|---|---|---|
| 1 | **What is v3.1 *about*?** A release needs a sentence, or it becomes whatever got finished | **"Batteries you reach for on day two"**: testing (#26), retries (#27), presets (#38), approval nodes (#36), MCP filters (#35). Correctness fixes ride along; everything speculative waits |
| 2 | **#211 — where a reporter comes from.** Explicitly deferred to "after this release", and it is now after | Take it early. It is a design question, and design questions block implementation, not the reverse |
| 3 | **#131 — what does the simplification pass mean now?** It was folded into QA, and QA happened as #219 | Rescope to what #219 actually surfaced, or close it. An open-ended sweep with no findings attached is the churn it was deferred to avoid |
| 4 | **#227 — is `asgi()` a demo surface or a real one?** | Decide before anyone writes a context factory. Both answers are defensible; shipping neither is not |
| 5 | **#225 — build-me-the-thing.** Big, and it fights every guard v3.0.0 added | Not v3.1. It needs its own design pass, and #219's ledger should be read first |
| 6 | **#129 — protocols (A2A, MCP server, OpenAI-compatible).** | v3.2 "rooms & reach", per the PRD's own naming. It is a surface expansion, not a battery |

## 4. The proposed split

The PRD (§6) already names three phases. Sorting the backlog into them is what turns thirty-five
issues into three releases.

### v3.1 — batteries · 14 issues

Additive on the frozen API, each one something a user reaches for on their second day.

- **Foundations first:** #26 (testing harness) — everything downstream is cheaper after it
- **Correctness:** #178, #177, #212, #128, #217, #223
- **Batteries:** #27 (retries), #38 (hosted-tool presets), #36 (approval/action nodes), #35 (MCP filters), #167 (`Preset`)
- **Design debt:** #211 (reporter), #227 (`asgi()` ruling)

### v3.2 — rooms & reach · 6 issues

Everything that widens who can reach a deck, and from where.

- #129 protocols · #34 per-user MCP credentials · #25 serve auth · #46 steering · #213 two decks · #24 inbox scale

These belong together: they are all "more than one caller", and solving them one at a time
produces four incompatible answers to the same identity question.

### v3.3 — operate · 3 issues

- #218 trace nesting · #37 skill installation · #20's script half (behind #163)

### Parallel, never release-blocking

**docs-site**: #224 (health check), #133 (coverage), #135 (design pass), #140 (Docusaurus).
**Unmilestoned by ruling**: #163 (sandboxing), which #20's remainder and #37 both wait on.

### Needs its own design pass before it is scheduled

**#225**, **#226**, **#228**, **#131** — each is a question before it is a task.

## 5. Dependency graph

```
        #26 testing harness  ─────────────┐  (cheapens every test below)
                                          ▼
v3.1    #178 #177 #212 #128 #217 #223   correctness
        #27 #38 #36 #35 #167            batteries
        #211 ─► reporter design          #227 ─► asgi ruling
                                          │
        ┌─────────────────────────────────┘
        ▼
v3.2    #25 auth ─┬─► #34 per-user MCP creds
                  └─► #129 protocols
        #46 steering ·  #213 two decks ·  #24 inbox scale
                                          │
        ▼
v3.3    #218  ·  #163 ─► #20 scripts, #37 install
```

Hard edges: #26 gates nothing formally but discounts everything; #34 needs #25's identity answer;
#20's remainder and #37 both wait on #163; #211 and #227 are rulings that gate their own
implementations.

## 6. Housekeeping before anyone starts

Five issues need their text corrected so the next person does not implement against a tree that
no longer exists: **#24**, **#35**, **#37**, **#38**, **#20**. Three should be closed with a
comment explaining what replaced them: **#28**, **#43**, **#44**.
