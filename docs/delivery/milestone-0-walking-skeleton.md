# Milestone 0  -  Walking Skeleton

**Done  -  verdict GO** (§6). Validate the core design (event schema, Runtime, engine boundary,
two-store rule, run control) against three adversarial use cases *before* committing to the full
epic: each exists to break a specific decision, so a fired falsifier means redesign at spike cost
instead of epic cost. Timebox 1–2 weeks, one developer, real Phase-1/skinny-Phase-2 code that earns
no polish until it survives the three demos. **Relates to:** `agentdeck-v2-architecture.md`,
`adr-d5-two-stores.md`, `epic-agentdeck-v2-core.md`.

## 1. Scope

| | |
|---|---|
| Build for real | `core/events.py` + `core/content.py` (the schema per design decisions 1–9, A=contiguous, B=full-text  -  **confirmed**, and UC1/UC3 still test both empirically); `core/context.py` + `core/status.py`; a minimal `Runtime` (stamp, append, fan out, yield); crude openai-agents and langgraph adapters behind `EnginePort`; memory + SQLite event stores; memory `ControlPort` with a SQLite-backed signal row for UC3's cross-process case; one SSE endpoint; a ~50-line CLI chat renderer as the switch-loop consumer |
| Fake shamelessly | Models (scripted, deterministic, no API keys, no network); no Redis, MCP, ACP, auth or discovery (three hardcoded invocables), no compat facade, no dashboards. Listed so nobody mistakes an absence for a design gap |
| Deferred deliberately | Caller-injected capabilities / ACP  -  the most self-contained pattern, proven on paper in design doc §9. If the skeleton finishes early it becomes UC4 rather than expanding UC1–3 |

## 2. Use case 1  -  "The handoff chat" (stresses the schema + ADR-D5)

`FrontDesk` (openai-agents, scripted fake) with `handoffs=[ClaimsAgent]`, which has one fake tool
`lookup_shipment`; SQLite store; CLI renderer on the SSE stream. Turn 1 hands off and the tool
answers; turn 2 in the same session is answered from turn-1 context (the fake asserts it received the
full prior transcript, tool result included); then the transcript is read back from the store alone.

- Two labeled bubbles, never one smeared paragraph  -  the renderer distinguishes speakers **using only `origin` + `message_id`**; a second mechanism found by grep means the envelope fields are insufficient, which is a falsifier.
- The transcript rebuilds from `message.completed` events **alone**, no delta assembly anywhere in the reader (decision B in practice), and the **transcript-fidelity test** (ADR-D5) runs for the first time: transcript from the SDK session ≡ transcript from the event log, in content and order. If it needs byte-level normalization the invariant is wrongly specified  -  stop.
- Turn 2's captured model input holds the exact turn-1 items including the untruncated tool result  -  proving execution state, not the log, fed the model  -  while the **log** carries only preview + hash + size (decision 7). Every event round-trips against the schema; `seq` is contiguous from 0 per run.

**Amendment (2026-08-05, #52 review  -  design finding, not fixed here).** The first bullet is red as
literally written: `origin` is invocable-scoped, stamped `origin = spec.name` for every event of a run
including after an internal handoff, so FrontDesk's sentence and ClaimsAgent's answer share one
`origin`  -  `message_id` still yields two bubbles, but `origin` cannot say ClaimsAgent spoke second.
The fix is either an engine-supplied speaker attribution the envelope has no field for, or a
deliberate redefinition of "speaker" as *the invocable*  -  deferred to the falsifier review (§6) /
`milestone-0-findings.md` rather than decided silently in the adapter PR. `tests/test_uc1_handoff.py`
asserts the shipped behavior (both bubbles labeled `FrontDesk`, distinct `message_id`s) so the gap
fails loudly.

**Ruling (2026-08-05, M0 checkpoint, issue #57).** Option B: invocable-scoped `origin` is the
contract, not a gap  -  "speaker" means *the invocable the caller addressed*, never the SDK's internal
sub-agent. Analysis in `milestone-0-findings.md` §3; the contract is stated in
`agentdeck/core/events.py`'s `origin` docstring.

## 3. Use case 2  -  "The Friday approval" (stresses durability + engine substitutability)

`ClaimPipeline`: two langgraph nodes with an approval interrupt between them, SQLite checkpointer,
same store, renderer and serve process as UC1. Node A runs and interrupts; `kill -9` the server and
restart it; `GET /pending` lists the interrupt with its payload; `POST /resume {thread_id, value}`
runs node B to completion.

- **Zero edits to the UC1 consumers**  -  `node.updated` falls through the renderer's default case harmlessly. Any needed edit means the engine abstraction leaked; verify by diff, not impression.
- Status transitions in order `RUNNING → WAITING_HUMAN → RUNNING → COMPLETED`, read from persistence after the kill; the resumed run's events continue in the **same session's** append order, one coherent story with no duplicate node-A events (or, if node-A re-execution is visible, matching the safe-point contract decided below).
- Exactly one terminal event and it is last, even across the restart; `seq` continues **contiguously across the restart**  -  no reset, no gap  -  because resume recovers the counter via `max(seq)` from the store.
- **Double-resume race:** of two concurrent `resume` calls exactly one wins the atomic `WAITING_HUMAN → RUNNING` transition, the other is a no-op, no `seq` duplicates; a stray `resume` to a completed run is likewise a no-op, not an error. The checkpointer stayed engine-private  -  nothing outside `adapters/engines/langgraph/` imports or reads it (linter + grep).

**Amendment (2026-08-05, #53  -  safe-point contract, decided here as the doc asked).**
`ClaimPipeline`'s interrupting node calls `interrupt()` as its first statement, so a resumed run
re-enters the node from its start with nothing to repeat: node A never re-executes and no event
appears twice. The contract for any interrupting node in this codebase  -  put the `interrupt()` call
first, and every side effect either before that node or after the value it returns, never before the
call in the same node  -  is the rule v1's workflow interrupts already document, written down for the v2
engine too.

**Amendment (2026-08-05, #53  -  double-resume guard is process-local).** The atomic transition is a
lock keyed by `run_id` inside one `Runtime` instance, not a store-level compare-and-set: correct for
two callers racing one process, wrong for two processes racing one run through two `Runtime`s over one
store. That needs a CAS primitive on `SessionStorePort`, which the frozen ports lack  -  follow-up work
if a real deployment needs it, recorded rather than silently assumed.

## 4. Use case 3  -  "The rude interruption" (stresses control + ordering guarantees)

A deliberately slow agent  -  the scripted fake emits 30 text chunks with small sleeps  -  and a
SQLite-backed `ControlPort` so a second process can signal. Terminal A streams the chat; terminal B
runs `agentdeck runs signal <run_id> cancel`; terminal A ends promptly with `run.cancelled`; then the
session is replayed from the store.

- `run_id` was obtainable by terminal B (from `run.started` in the stream or a runs listing)  -  addressability is real, not theoretical  -  and cancel lands at the **next safe point**: the last delta before `run.cancelled` is a complete chunk, nothing follows the terminal event, under adversarial timing (run the script 20×; flakiness here is a real bug, not test noise).
- The gate raised cooperatively inside the adapter, `run.cancelled` was emitted exactly once, status is terminal, a follow-up `pause` is a no-op, and replay shows truncated-but-coherent history  -  N deltas, no `message.completed` for the unfinished message, terminal `run.cancelled`  -  with the renderer marking the unfinished bubble rather than crashing.
- **The chaos test (decision A, mandatory):** drop one mid-run event before it reaches the consumer; the consumer must *detect* the `seq` gap and refetch from the store, proving contiguity buys loss-detection in practice.

**Amendment (2026-08-05, #54  -  as built).** Zero edits landed in `surfaces/serve/app.py` or
`surfaces/cli/chat.py`: `Runtime.run`/`resume` rebind `ctx.gate` to a real `Gate` only when the
`Runtime` was built with a `ControlPort`, so the chat route became cancellable without knowing control
exists. The 20×-loop and the cross-process script are two tests deliberately  -  the loop signals
in-process, while the cross-process test spawns a real `python -m agentdeck.cli` subprocess once to
prove addressability and the SQLite signal row crossing a process boundary. That subprocess costs over
a second just importing the package (`agentdeck/__init__.py` eagerly imports v1's `App`, which pulls in
`langgraph`), so its SlowPoke fixture sleeps 0.2s/chunk rather than the in-process 0.005s. And
`httpx.ASGITransport` runs a request's whole ASGI call before returning anything, so cancelling the
chat route is proven by architecture  -  the same `Runtime._with_gate` every other test exercises.

## 5. Build order with per-step gates

Do not proceed past a red gate. If one stays red for more than a day of honest effort, treat it as a
design finding: write down what the design assumed and what reality said, and bring it back to the
docs before coding around it.

| Step | Built | Gate |
|---|---|---|
| 1 | `events.py`, `content.py`, serialization round-trips per kind, `UnknownEvent` fallback, contiguous-`seq` invariant | Forward-compat: an event with an unknown kind and unknown field deserializes, persists, and is skipped by a toy consumer |
| 2 | `Runtime` + memory store + stub engine; contract-suite skeleton (one terminal event, terminal-is-last, seq contiguity) | Contract suite green on the stub; killing a consumer mid-stream does not corrupt the store |
| 3 | openai-agents adapter (crude) + SQLite store + renderer → UC1 | Every "make sure" item in §2, especially transcript fidelity |
| 4 | langgraph adapter (crude) + status persistence → UC2 | Every item in §3, especially the zero-consumer-edits diff; contract suite green on **both** engines  -  the first moment the architecture is real |
| 5 | `ControlPort` + gate → UC3 | Every item in §4, including the 20× loop and the chaos test |

## 6. At the finish

**Demo artifact.** The three scripts as one continuous demo (UC1 chat → UC2 kill/restart/approve → UC3
cross-process cancel + gap-detection)  -  the proof object for the go/no-go.

**Falsifier review.** The skeleton *fails*, and the epic pauses for redesign, if any of these occurred
even once, even temporarily:

- Any consumer needed to know which engine produced an event.
- The schema needed a new **required** field after UC1 events were already persisted (additive evolution broken in practice).
- UC2 required editing any UC1 consumer.
- Transcript fidelity was only achievable with byte-level normalization.
- Exactly-one-terminal-event needed consumer-side workarounds (timeouts, dedupe).
- The gap-detection test could not be made reliable.

If no falsifier fired, three deliverables: the **schema-as-built diff** against design doc §4.2 + the
nine review decisions, one line per divergence, which *is* the PR #1 content rather than the paper
schema; the **learning note**, ranked by "would have been expensive to learn in Phase 2", with the
amendments it forces on the design doc, ADR-D5 and the epic and a decision log for anything decided ad
hoc; and an explicit per-component **keep / harden / discard**, because nothing keeps by default and
skeleton code that sneaks into production unreviewed is how spikes rot into foundations.

**Closing note (2026-08-05, #57  -  the finish checkpoint). Verdict: GO.** No falsifier fired; all three
deliverables are in `milestone-0-findings.md`, and the demo artifact is `scripts/m0_demo.py`  -
deterministic and replayable rather than a recording, running UC1 → UC2 → UC3 against real SQLite
stores and the real `surfaces/serve` FastAPI apps with scripted fakes only. The epic proceeds to Story 2
hardening (re-sequenced in `epic-agentdeck-v2-core.md`'s own 2026-08-05 amendment), and §2's
origin/speaker finding is ruled Option B.
