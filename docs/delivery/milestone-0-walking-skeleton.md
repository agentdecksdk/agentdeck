# Milestone 0 — Walking Skeleton

**Status:** proposal · **Relates to:** `agentdeck-v2-architecture.md`, `adr-d5-two-stores.md`, `epic-agentdeck-v2-core.md`
**Purpose:** validate the core design (event schema, Runtime, engine boundary, two-store rule, run control) against three adversarial use cases *before* committing to the full epic. Each use case exists to break a specific decision. If all three survive, the epic is execution; if one falsifier fires, we redesign at spike cost instead of epic cost.

**Timebox:** 1–2 weeks, one developer. The skeleton is real Phase-1/skinny-Phase-2 code, not throwaway — but it earns no polish until it survives the three demos.

---

## 1. Scope

**Build for real:** `core/events.py` + `core/content.py` (the schema per design review decisions 1–9, A=contiguous, B=full-text — **confirmed**; UC1/UC3 still test both empirically: store-only transcript rebuild, chaos gap-detection); `core/context.py` + `core/status.py`; a minimal `Runtime` (stamp, append, fan out, yield); crude openai-agents and langgraph adapters behind `EnginePort`; memory + SQLite event stores; memory ControlPort (SQLite-backed signal row for cross-process in UC3); one SSE endpoint; a ~50-line CLI chat renderer (the switch-loop consumer).

**Fake shamelessly:** models (scripted fakes, deterministic, no API keys, no network); no Redis, no MCP, no ACP, no auth, no discovery (hardcode the three invocables), no compat facade, no dashboards. Every fake is listed here so nobody mistakes its absence for a design gap.

**Deferred deliberately:** caller-injected capabilities / ACP. It is the most self-contained pattern (proven on paper in design doc §9); if the skeleton finishes early, add it as UC4 rather than expanding UC1–3.

---

## 2. Use case 1 — "The handoff chat" (stresses the schema + ADR-D5)

**Setup.** `FrontDesk` (openai-agents, scripted fake model) with `handoffs=[ClaimsAgent]`. `ClaimsAgent` has one fake tool `lookup_shipment`. SQLite store. CLI chat renderer attached to the SSE stream.

**Script.**

```text
1. POST /agents/FrontDesk/chat?session_id=s1&stream=true  "my shipment 4412 is damaged"
   → FrontDesk speaks one sentence, hands off; ClaimsAgent calls the tool, answers.
2. Same session, second turn: "and when will the refund arrive?"
   → ClaimsAgent answers using turn-1 context (the fake model script asserts it
     received the full prior transcript, including the tool result).
3. Read the transcript back from the store only (no live stream).
```

**Make sure at this step:**

- Two labeled bubbles, never one smeared paragraph — the renderer distinguishes speakers **using only `origin` + `message_id`** from the envelope; grep the renderer for any other mechanism (a second mechanism means the envelope fields are insufficient — falsifier).
- Step 3 rebuilds the transcript from `message.completed` events **alone** — no delta assembly anywhere in the reader (decision B holds in practice).
- Run the **transcript-fidelity test** (ADR-D5) for the first time: message-level transcript extracted from the SDK session ≡ transcript from the event log, in content and order. If passing requires byte-level normalization hacks, the invariant is wrongly specified — stop.
- Turn 2's model input (captured by the fake) contains the exact turn-1 items including the untruncated tool result — proving execution state, not the log, fed the model (the ADR's whole point, observed live).
- Tool result in the **log** shows preview + hash + size (decision 7), while the **SDK session** holds full bytes.
- Every event validates against the schema round-trip; `seq` is contiguous from 0 per run.

## 3. Use case 2 — "The Friday approval" (stresses durability + engine substitutability)

**Setup.** `ClaimPipeline`: two langgraph nodes with an approval interrupt between them; SQLite checkpointer; same store, same renderer, same serve process as UC1.

**Script.**

```text
1. POST /workflows/ClaimPipeline?stream=true  → node A runs → run.interrupted(reason=approval)
2. kill -9 the server process. Restart it.
3. GET  /pending  → the interrupt is listed with its payload
4. POST /resume {thread_id, value: approve}  → node B runs → run.completed
```

**Make sure at this step:**

- **Zero edits to the UC1 consumers.** The CLI renderer and SSE endpoint render this workflow run as-is; `node.updated` falls through the renderer's default case harmlessly. Any needed edit = the engine abstraction leaked = falsifier. Verify by diff, not by impression.
- Status transitions observed in order: `RUNNING → WAITING_HUMAN → RUNNING → COMPLETED`; after the kill, status reads `WAITING_HUMAN` from persistence, not from memory.
- The resumed run's events continue in the **same session's** append order; replay after completion shows one coherent story across the restart (interrupt event, then resume, then node B) with no duplicates for node A (or, if node-A re-execution is visible, it matches the documented safe-point contract — decide and write it down here).
- Exactly one terminal event, and it is last — even across the restart.
- `seq` continues **contiguously across the restart** — no reset to 0, no gap: on resume the Runtime recovers the counter via `max(seq)` from the store (invariant 3 of the seq design).
- **Double-resume race:** fire two concurrent `resume` calls for the same interrupt — exactly one wins (atomic `WAITING_HUMAN → RUNNING` transition); the other is a no-op; no duplicate seq values in the log.
- The checkpointer stayed engine-private: nothing outside `adapters/engines/langgraph/` imports or reads it (linter + grep).
- Send a stray `resume` to the already-completed run afterward → no-op per the status machine, not an error.

## 4. Use case 3 — "The rude interruption" (stresses control + ordering guarantees)

**Setup.** A deliberately slow agent: scripted fake emits 30 text chunks with small sleeps. SQLite-backed ControlPort so a second process can signal.

**Script.**

```text
1. Terminal A: POST /agents/SlowPoke/chat?stream=true  → chunks flowing
2. Terminal B (separate process): agentdeck runs signal <run_id> cancel
3. Terminal A: stream ends promptly with run.cancelled
4. Replay the session from the store.
```

**Make sure at this step:**

- `run_id` was obtainable by terminal B (from `run.started` surfaced in the stream / a runs listing) — addressability is real, not theoretical.
- Cancel lands at the **next safe point**: the last delta before `run.cancelled` is a complete chunk; nothing is emitted after the terminal event, under adversarial timing (run the script 20× in a loop — flakiness here is a real bug, not test noise).
- The gate raised cooperatively inside the adapter; the adapter emitted `run.cancelled` exactly once; status is terminal; a follow-up `pause` signal is a no-op.
- Replay shows the truncated-but-coherent history: N deltas, no `message.completed` for the unfinished message, terminal `run.cancelled` — and the renderer copes (unfinished bubble marked, not crashed).
- **The chaos test (decision A, mandatory):** intercept the stream and drop one mid-run event before it reaches the consumer; the consumer must *detect* the `seq` gap and recover by refetching from the store — proving contiguity buys loss-detection in practice, not just in argument.

---

## 5. Build order with per-step gates

Each step has a gate; do not proceed past a red gate.

**Step 1 — schema + round-trips.** `events.py`, `content.py`, serialization round-trip per kind, unknown-kind fallback (`UnknownEvent`) test, contiguous-`seq` invariant test. *Gate:* forward-compat test passes — an event with an unknown kind and unknown field deserializes, persists, and is skipped by a toy consumer.

**Step 2 — Runtime + memory store + stub engine.** The stamp/append/fan-out/yield loop against a scripted stub engine; contract-suite skeleton with the first invariants (one terminal event, terminal-is-last, seq contiguity). *Gate:* contract suite green on the stub; killing a consumer mid-stream does not corrupt the store.

**Step 3 — openai-agents adapter (crude) + SQLite store + renderer → run UC1.** *Gate:* every "make sure" item in §2, especially the transcript-fidelity test.

**Step 4 — langgraph adapter (crude) + status persistence → run UC2.** *Gate:* every item in §3, especially the zero-consumer-edits diff; contract suite now green on **both** engines — the first moment the architecture is real.

**Step 5 — ControlPort + gate → run UC3.** *Gate:* every item in §4, including the 20× loop and the chaos test.

If any gate stays red for more than a day of honest effort, treat it as a design finding, not an implementation struggle: write down what the design assumed and what reality said, and bring it back to the docs before coding around it.

---

## 6. At the finish

**Demo artifact.** Record the three scripts as one continuous demo (UC1 chat → UC2 kill/restart/approve → UC3 cross-process cancel + gap-detection). This is the proof object for the epic go/no-go.

**Falsifier review — the go/no-go checklist.** The skeleton *fails* (and the epic pauses for redesign) if any of the following occurred, even once, even "temporarily":

- Any consumer needed to know which engine produced an event.
- The schema needed a new **required** field after UC1 events were already persisted (the additive-evolution rule is broken in practice).
- UC2 required editing any UC1 consumer.
- Transcript fidelity was only achievable with byte-level normalization.
- Exactly-one-terminal-event needed consumer-side workarounds (timeouts, dedupe).
- The gap-detection test could not be made reliable.

**Schema freeze candidate.** If no falsifier fired: diff the schema as-built against design doc §4.2 + the nine review decisions; every divergence gets a one-line justification. This diff **is** the PR #1 content — the skeleton's surviving schema is what goes to formal review, not the paper version.

**The learning note.** One page, `milestone-0-findings.md`: what the skeleton taught us, ranked by "would have been expensive to learn in Phase 2"; amendments to the design doc, ADR-D5, and the epic (especially Story 2's estimate, now grounded in the crude adapters' actual difficulty); and the decision log for anything decided ad hoc during the spike (e.g. UC2's node re-execution visibility) that must be promoted to a documented contract.

**Disposal decision, made explicitly.** For each skeleton component: *keep* (schema, core, contract suite — these were always Phase 1), *harden* (adapters, Runtime — real error handling in Phase 2), or *discard* (hardcoded registry, CLI renderer beyond its life as a reference consumer). Nothing keeps by default; skeleton code that sneaks into production unreviewed is how spikes rot into foundations.
