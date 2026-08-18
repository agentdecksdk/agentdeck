# ADR-D5 (revised)  -  Two stores: the Event Log and Engine Execution State

**Status:** accepted (supersedes D5 as written in `agentdeck-v2-architecture.md` §12)
**Date:** 2026-08-03 · **Relates to:** design doc §4.2, §5, §11 Story 2; epic Story 2/3b

---

## 1. Decision, in one line

The **event log** (`adapters/stores/`) is the authoritative *record* of what happened,
read by every platform consumer. **Engine-native execution state**  -  the OpenAI Agents
SDK's session (Redis/SQLite) and LangGraph's checkpointer  -  is the authoritative *working
memory* of each engine's loop, private to its adapter. Both are first-class; neither is
derived from the other; a transcript-fidelity invariant ties them together and is
enforced by a contract test.

```text
                       ┌────────────────────────────┐
   every consumer ───▶ │ EVENT LOG  (adapters/stores)│  platform-facing record
   SSE · replay ·      │ append-only, uniform,       │  audit · cost · ACP session/load
   audit · dashboard   │ canonical Event schema      │  dashboard · evals
                       └────────────▲───────────────┘
                                    │ adapter reports everything that
                                    │ entered/left the loop (invariant)
┌──────────────────────┐   ┌────────┴─────────────────┐
│ openai-agents adapter │   │ langgraph adapter        │
│  SDK session (Redis/  │   │  checkpointer (sqlite/   │   engine-facing working memory
│  SQLite): exact model-│   │  postgres): graph state, │   one reader each: the loop
│  facing items         │   │  thread_id resume points │
└──────────────────────┘   └──────────────────────────┘
```

---

## 2. Context: what the original D5 said, and why it is wrong

Original D5: *"The event log is the source of truth; engine session state is a derived
projection."* Under that rule the openai-agents adapter must rebuild the SDK's session
input from the log on every turn (or maintain a projection cache invalidated by `seq`).

Two problems, one empirical and one structural.

**The empirical problem  -  silent fidelity loss.** The SDK session stores the exact
model-facing items: reasoning items, tool-call items with paired IDs, handoff items, in
the SDK's own wire format. The canonical events are deliberately lossy projections for
consumers. A worked trace makes the failure concrete:

*Turn 1.* User asks "What's in `report.pdf`?" The agent calls `read_file`, then answers.

```text
SDK session (execution state)                 Event log (record)
──────────────────────────────                ─────────────────────────────────────
1. user_message   "What's in report.pdf?"     input.appended        "What's in…"
2. reasoning_item rs_042 "read it first…"     (not logged  -  by design)
3. tool_call      call_7f3a read_file(...)    tool.call.started     call_7f3a
4. tool_result    call_7f3a <4,000 chars>     tool.call.completed   (truncated)
5. assistant_msg  "The report covers Q2…"     message.completed     "The report…"
```

*Turn 2 under original D5.* The adapter reconstructs items 1–5 from the right-hand
column. `rs_042` does not exist there; the tool result is truncated. Nothing crashes  -
the model simply receives an *almost*-correct history: missing reasoning items that
newer models reference across turns, an incomplete tool result, or an SDK validation
error when `call_7f3a`'s result no longer pairs with a byte-exact call item. The agent
gets quietly dumber. The only escape is bloating the event schema into a mirror of the
SDK's internal item format  -  at which point every SDK release forces an event-schema
change, destroying the "small stable contract" property that is the entire point of §4.2.
It also costs O(history) reconstruction per turn.

**The structural problem  -  the rule was never applied consistently.** LangGraph's
checkpointer was placed inside its adapter as engine-private durable state without a
second thought, and nobody proposed deriving graph state from the event log. Same
situation, opposite ruling. The LangGraph treatment was the correct one; original D5 made
a lossy summary the master copy and asked the adapter to un-lose the loss.

This is the database + audit stream pattern (Postgres + Kafka), not the conversation stored
twice: two projections with different owners, schemas, readers and lifecycles, which only *look*
like duplicates because both contain sentences. The design space has three options  -  log-only (the
fidelity loss above), engine-state-only (every platform feature parses N engine-native formats),
or both with a testable overlap. This ADR chooses the third.

---

## 3. The revised decision, in full

**D5 (revised).** Every engine adapter owns private, engine-native execution state in
whatever format its loop requires, keyed by `session_id` / `thread_id`, written only by
that adapter. The event log remains the sole platform-facing record and the only thing
any consumer, surface, or protocol adapter may read. Rebuilding execution state from the
log is demoted to best-effort disaster recovery and is never the normal path.

For the openai-agents adapter concretely: the SDK's session managers (including the
Redis session manager in newer SDK releases) are *used as designed*, inside
`adapters/engines/openai_agents/`. The existing `runtime/sessions.py` `SessionFactory`
is therefore largely **kept, not rewritten**  -  it relocates into that adapter as its
execution store. Adopting a newer SDK session manager becomes an adapter-internal
version bump; nothing outside the directory notices. For the langgraph adapter: the
checkpointer stays exactly where Story 2 already puts it. The two engines are now
symmetric.

**The invariant that replaces "derived projection":** *everything that enters or leaves
execution state must be recorded in the log*  -  every user input (including mid-turn
`input.appended` from Story 3b), every tool call and result, every completed assistant
message, every interrupt and resume value. Fidelity is required at the
**transcript/message level, not the byte level**: the log may truncate a tool result for
storage, but it may never omit that the call happened, and it may never omit or reorder
a message.

**Write ordering.** Log first (intent), engine state second (execution). Both writes
happen in the same turn, single writer (the adapter), append-only log  -  there is no
bidirectional sync and no distributed-consistency machinery; this is a WAL pattern. A
crash between the two writes leaves an `input.appended` with no engine-side counterpart;
the adapter reconciles on the next turn by detecting log entries newer than its
execution state and replaying them into it (this narrow, message-level replay is safe
precisely because inputs are not lossy in the log).

**Contract test (added to the shared suite, both engines):** run a multi-turn
conversation with tool calls and an interrupt/resume; extract the message-level
transcript from the engine's execution state and from the event log; assert they are
identical in content and order. This replaces the earlier "rebuild-from-log equals cached
session, byte for byte" criterion, which is now recognized as untestable-because-false.

**Operational separation.** The same Redis instance may back both `adapters/stores/redis`
(the log) and the SDK session (execution state), but in separate keyspaces with separate
lifecycle policies  -  e.g. log retained for audit horizons (a year), execution state
expired after idle periods (30 days). Deleting execution state must never delete the
record; an expired session simply means the next turn starts a fresh loop memory while
`session/load` and replay still work from the log.

---

## 4. Consequences

*Gained:* byte-exact model context across turns (reasoning items and paired tool IDs
intact); the event schema stays small and SDK-version-independent; `SessionFactory` code
is preserved; engine symmetry (one rule, no exceptions); SDK session-manager upgrades are
contained to one directory; a checkable invariant instead of an aspirational one.

*Costs, recorded honestly:* modest storage overlap (the message transcript exists in
both stores); the log alone cannot byte-reproduce model inputs, so evals requiring exact
model context must snapshot engine state or accept transcript-level fidelity; and the
reconciliation path (crash between the two writes) is code that must exist and be tested,
even though it is small.

*Explicitly unchanged:* consumers still read only the log; surfaces and protocol
adapters have no access to execution state; `Runtime` still stamps and appends every
event; ACP `session/load` still replays from the log.

> **Amended 2026-08-08 (ADR-D11, #149).** "`Runtime` still stamps and appends every event" no
> longer holds: the store assigns `seq` and `ts` in the same atomic step that persists the event,
> and the Runtime hands it payloads. Everything else in this clause stands, and so does this
> ADR's two-store rule  -  D11 changes who fills the envelope, not what is stored where.

---

## 5. Amendments to the existing documents

Applied 2026-08-04 to `agentdeck-v2-architecture.md` (§12 D5, §5's openai-agents paragraph, §11's
`runtime/sessions.py` migration row) and to `epic-agentdeck-v2-core.md` (Story 2's
transcript-fidelity and crash-reconciliation criteria; Story 3b's write ordering). Ledger:
`00-project-index.md` §3.

---

## 6. Amendment 2026-08-05  -  reconciliation as built (issue #76)

Reconciliation lives in `adapters/engines/openai_agents/reconcile.py`, called at turn start
before `Runner.run_streamed`. Where the built thing differs from §3, or costs what §3 did not
name:

| as built | detail |
|---|---|
| Both roles replay, and `input.appended` has no producer | §3 named an `input.appended` with no engine-side counterpart. A turn's input reaches the log on `run.started`, and the SDK saves output items *after* they stream  -  so the same window loses assistant messages too. The replay covers every `run.started`/`input.appended` input and every `message.completed`: the same message-level transcript the fidelity test compares |
| The comparison is a prefix check | Both transcripts are built, verified to agree on their common prefix, and the remainder appended as plain `{"role", "content"}` items |
| Divergence *within* the prefix is reported, never repaired | The session is the authority on execution, and a wrong guess about its tail is worse than the gap, so it is left untouched and the run emits `custom(openai_agents.session_diverged)` with both message counts. A session merely *ahead* of the log is silent |
| Message level is the ceiling | Tool calls, results and reasoning items are never replayed  -  the log's copies are truncated or absent, exactly as §3 says |
| §4's "byte-exact model context" is conditional | A repair writes plain text where the session held paired tool-call/result and reasoning items, and keeps holding plain text for the rest of the conversation: byte-exact across turns *until* a crash forces a repair, transcript-level after one. Accepted, since the alternative is a turn the model cannot see at all  -  and a deployment needing the stronger property treats the divergence event as the signal |
| An abandoned turn's input is skipped  -  cancelled **and** no `message.completed` | `run.started` records that a turn was asked for, not taken. An SSE disconnect before the engine read anything leaves a question the user is about to re-ask, and replaying it lands a copy in front of the retry. The conjunction is load-bearing: the SDK persists a turn's input and output together, so dropping just the input of a turn cancelled *after* its answer would misalign the transcripts mid-stream, report a divergence that never heals, and disable reconciliation for that session from then on |
| Two limits of reading acceptance off the log | An input the engine *rejected* logs `run.failed` (`_to_sdk_input` refuses non-text blocks), indistinguishable from a dead session write, so it is replayed next turn. A turn cancelled after a *tool call* but before a message is persisted by the SDK while logging no `message.completed`, so it is treated as abandoned and misaligns the same way. Both root in the log recording what was asked and produced, never what the SDK chose to persist. The general fix  -  accept either transcript as a valid prefix and replay against the strict one  -  is written down here rather than built |
| Concurrency is single-process | Read-then-append is atomic under a per-session `asyncio.Lock`, so two turns racing on one session in one server cannot both apply the repair. Two servers on one session need no cover: #83 rejects concurrent turns at the door with an atomic session claim |
| An emptied session is refilled, not left blank | An empty session against a non-empty log is indistinguishable from a crash on the first turn, so it takes the same repair. §3's "best-effort disaster recovery" reached automatically, costing one full replay once |
| LangGraph gets no code | Its checkpointer write *is* the graph step, so there is no second write to crash between, and a run's thread is its own (`thread_id = run_id`), so a lost turn cannot poison a later one |

**The one thing that is worse than unrepairable: a stranded resume.** The conditional append that claims a resume records
`run.resumed` and flips the run `WAITING_ANSWER` → `RUNNING` *before* the engine sees the resume
value, which the log does not carry (the payload holds only `reason`). A crash in that window
leaves the log saying `RUNNING` while the checkpointer is still parked at the interrupt: replay
cannot help, because §3's safety condition  -  inputs are not lossy in the log  -  does not hold for a
value the log never had, and every later resume is refused as stray. The run can never be
continued at all. Tracked as #94; recovering it needs a recorded resume value or a way back to
`WAITING_ANSWER`, both decisions of their own.
