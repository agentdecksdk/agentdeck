# ADR-D5 (revised) — Two stores: the Event Log and Engine Execution State

**Status:** accepted (supersedes D5 as written in `agentdeck-v2-architecture.md` §12)
**Date:** 2026-08-03 · **Relates to:** design doc §4.2, §5, §11 Story 2; epic Story 2/3b

---

## 1. Decision, in one line

The **event log** (`adapters/stores/`) is the authoritative *record* of what happened,
read by every platform consumer. **Engine-native execution state** — the OpenAI Agents
SDK's session (Redis/SQLite) and LangGraph's checkpointer — is the authoritative *working
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

**The empirical problem — silent fidelity loss.** The SDK session stores the exact
model-facing items: reasoning items, tool-call items with paired IDs, handoff items, in
the SDK's own wire format. The canonical events are deliberately lossy projections for
consumers. A worked trace makes the failure concrete:

*Turn 1.* User asks "What's in `report.pdf`?" The agent calls `read_file`, then answers.

```text
SDK session (execution state)                 Event log (record)
──────────────────────────────                ─────────────────────────────────────
1. user_message   "What's in report.pdf?"     input.appended        "What's in…"
2. reasoning_item rs_042 "read it first…"     (not logged — by design)
3. tool_call      call_7f3a read_file(...)    tool.call.started     call_7f3a
4. tool_result    call_7f3a <4,000 chars>     tool.call.completed   (truncated)
5. assistant_msg  "The report covers Q2…"     message.completed     "The report…"
```

*Turn 2 under original D5.* The adapter reconstructs items 1–5 from the right-hand
column. `rs_042` does not exist there; the tool result is truncated. Nothing crashes —
the model simply receives an *almost*-correct history: missing reasoning items that
newer models reference across turns, an incomplete tool result, or an SDK validation
error when `call_7f3a`'s result no longer pairs with a byte-exact call item. The agent
gets quietly dumber. The only escape is bloating the event schema into a mirror of the
SDK's internal item format — at which point every SDK release forces an event-schema
change, destroying the "small stable contract" property that is the entire point of §4.2.
It also costs O(history) reconstruction per turn.

**The structural problem — the rule was never applied consistently.** LangGraph's
checkpointer was placed inside its adapter as engine-private durable state without a
second thought, and nobody proposed deriving graph state from the event log. Same
situation, opposite ruling. The LangGraph treatment was the correct one; original D5 made
a lossy summary the master copy and asked the adapter to un-lose the loss.

**Why this is not "storing the conversation twice."** It is the standard database + audit
stream pattern (Postgres + Kafka). The engine state is the working set — private,
engine-formatted, optimized for "give the model its exact context." The log is the
record — public, uniform across engines, optimized for "what happened, in order, for any
consumer." They only *look* like duplicates because both happen to contain sentences;
they are different projections with different owners, schemas, readers, and lifecycles.
The design space has exactly three options, and two are broken: log-only (the fidelity
loss above), engine-state-only (every platform feature must parse N engine-native
formats — the bifurcation reinvented per engine and per SDK version), or both with a
testable overlap. This ADR chooses the third.

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
is therefore largely **kept, not rewritten** — it relocates into that adapter as its
execution store. Adopting a newer SDK session manager becomes an adapter-internal
version bump; nothing outside the directory notices. For the langgraph adapter: the
checkpointer stays exactly where Story 2 already puts it. The two engines are now
symmetric.

**The invariant that replaces "derived projection":** *everything that enters or leaves
execution state must be recorded in the log* — every user input (including mid-turn
`input.appended` from Story 3b), every tool call and result, every completed assistant
message, every interrupt and resume value. Fidelity is required at the
**transcript/message level, not the byte level**: the log may truncate a tool result for
storage, but it may never omit that the call happened, and it may never omit or reorder
a message.

**Write ordering.** Log first (intent), engine state second (execution). Both writes
happen in the same turn, single writer (the adapter), append-only log — there is no
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
lifecycle policies — e.g. log retained for audit horizons (a year), execution state
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

---

## 5. Amendments to the existing documents

`agentdeck-v2-architecture.md`: replace D5 in §12 with §3 of this ADR (one-paragraph
form); in §5, rewrite the openai-agents adapter paragraph — delete "the adapter derives
the SDK's session input from the event log … the SDK session is a projection" and state
that the adapter owns SDK-native session state via the relocated `SessionFactory`, with
the transcript invariant; in §11, change the `runtime/sessions.py` migration row's
destination from `adapters/stores/{sqlite,redis}` to
`adapters/engines/openai_agents/sessions.py` (the event-log stores are new code, not a
port of `SessionFactory`).

`epic-agentdeck-v2-core.md`: Story 2 — swap the session-related acceptance criterion for
"transcript-fidelity contract test passes on both engines (execution-state transcript ≡
log transcript)" and add "crash-between-writes reconciliation covered by an integration
test"; Story 3b (mid-turn injection), when added — `input.appended` is written to the log
before being drained into execution state, per the write-ordering rule above.

---

## 6. Amendment 2026-08-05 — reconciliation as built (issue #76)

Reconciliation now exists, in `adapters/engines/openai_agents/reconcile.py`, called at turn
start before `Runner.run_streamed`. Four points where the built thing differs from §3's
description:

**It is not only inputs, and not `input.appended`.** §3 says the crash "leaves an
`input.appended` with no engine-side counterpart". `input.appended` still has no producer;
a turn's input reaches the log on `run.started`, and the SDK writes its session copy inside
the run. The same window also loses *assistant* messages: the SDK saves a turn's output
items after they have streamed, so a log that already holds `message.completed` can outlive
the session write it belongs to. The replay therefore covers both roles — every
`run.started`/`input.appended` input and every `message.completed` — which is the same
message-level transcript the fidelity contract test compares.

**The comparison is a prefix check, and divergence is left alone.** The adapter builds both
message-level transcripts, verifies the session's is a prefix of the log's, and appends the
remainder as plain `{"role", "content"}` items. If the session is *not* a prefix of the log
it is left untouched with a warning: it is the authority on execution, and a wrong guess
about its tail (duplicated or reordered messages) is worse than the gap. Tool calls, tool
results and reasoning items are never replayed — the log's copies are truncated or absent,
so message level is the ceiling, exactly as §3 says.

**An emptied session is refilled, not left blank.** §3's operational-separation clause says
an expired session "simply means the next turn starts a fresh loop memory". As built, an
empty session against a non-empty log is indistinguishable from a crash on the session's
first turn, so it takes the same repair: the log's message-level transcript is replayed in.
That is §3's "best-effort disaster recovery" reached automatically rather than a blank
start, and it costs one full replay, once, on the first turn after the loss.

**LangGraph has no equivalent gap, and gets no code.** Its checkpointer write *is* the graph
step, so there is no second write for a crash to fall between: an input either entered a
super-step that committed or the step never happened. A run's thread is its own
(`thread_id = run_id`), so unlike the SDK session — shared across a session's turns — a lost
turn cannot poison a later one. The one place the two stores can disagree there is a resume:
`run.resumed` is claimed in the log before the engine sees the resume *value*, and that value
is not in the log (the payload carries only `reason`), so a crash in that window is not
repairable by replay at all — §3's safety condition ("inputs are not lossy in the log") does
not hold for it. Recording resume values is a schema change and a separate decision; it is
noted here so the absence is deliberate rather than overlooked.
