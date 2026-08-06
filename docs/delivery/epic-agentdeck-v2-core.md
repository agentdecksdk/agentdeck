# Epic: AgentDeck v2 — Unified Core & Runtime

**Reference:** `agentdeck-v2-architecture.md` · **Baseline:** agentdeck 1.2.1 (`60b95b6`)

## Epic summary

Restructure agentdeck from two parallel silos (agents / workflows) into a three-ring
architecture: a small zero-I/O core (events, RunContext, ports), engine adapters behind a
single lifecycle boundary, and thin surfaces that render one canonical event stream. The
epic ends with the first externally visible proof of the new architecture: pause/resume/
cancel on every run, caller-injected capabilities, and a working ACP surface — all
delivered without breaking the existing `.agentdeck/` project convention or the current
HTTP API.

**Why now:** every roadmap item (protocols, UI, cost, audit, multi-tenancy) is a consumer
of the event log and RunContext. Each story in this epic gets cheaper the earlier it
lands and dramatically more expensive after more features accrete on the current split.

**Epic-level definition of done**

- [ ] `make test` green after every story (each story is independently shippable)
- [ ] Existing public API (`App`, `run_agent`, `run_workflow`, `chat`, `chat_stream`) works unchanged via the compat facade
- [ ] Existing SSE wire format byte-preserved; existing `.agentdeck/` projects load with zero edits
- [ ] `import-linter` CI gate: `core/` imports nothing from `agents`, `langgraph`, `fastapi`, `redis`
- [ ] Contract-test suite runs against both engines and passes identically
- [ ] CHANGELOG entries per story; design doc updated where implementation diverged

**Out of scope for this epic:** AG-UI/A2A adapters, dashboard, stdlib/toolkit content
(Appendix A), auth beyond `Principal` on context, multi-tenant deployment. Tracked as
follow-up epics.

---

## Story 1 — Core nouns: events, RunContext, ports (Phase 1)

**As a** platform developer, **I want** the canonical Event schema, RunContext, and port
ABCs to exist and be threaded through the existing code paths, **so that** every
subsequent feature has a stable contract to build on — without changing any behavior.

**Scope.** Create `core/` (`events.py`, `content.py`, `context.py`, `invocable.py`,
`status.py`, `errors.py`, `ports/*`) with in-memory/no-op implementations for every
port. Introduce `Input = list[ContentBlock]` with a str→TextBlock coercion shim. Thread
`RunContext` (default single-tenant, generated run_id/trace_id, no-op gate, empty caps)
through `App.run_agent`, `App.run_workflow`, `App.chat`, and both runner call chains.
Stand up the contract-test suite skeleton (event ordering, exactly-one-terminal-event,
envelope invariants) — parametrized but running against a stub engine only for now.

**Acceptance criteria**

- [ ] Event union with envelope (`v`, `seq`, `run_id`, `session_id`, `tenant`, `ts`, `kind`) and all §4.2 kinds, with unit tests for serialization round-trips and unknown-kind tolerance
- [ ] `RunContext` is a required parameter on every internal run path; public API constructs a default context so user code is unaffected
- [ ] Run status machine in `core/status.py` with tests proving terminal-state signals are no-ops
- [ ] import-linter contract for `core/` active in CI
- [ ] No behavior change: full existing test suite passes untouched

**Estimate:** S–M. **Risk:** low — additive only. This is the cheapest story and the one
every other story depends on.

---

## Story 2 — The seam: EnginePort, Runtime, adapters, serve rewrite (Phase 2)

**As a** platform developer, **I want** both SDK integrations moved behind `EnginePort`
emitting canonical events, orchestrated by one `Runtime` service, **so that** the
agent/workflow bifurcation is eliminated and every consumer stops caring which engine ran.

**Scope.** Convert `HeadlessRunner` into `adapters/engines/openai_agents/` yielding event
payloads (reusing the `runtime/events.py` extraction helpers); move `workflows/*` +
`runtime/checkpointer.py` into `adapters/engines/langgraph/` mapping `astream`/`interrupt`
onto `node.updated`/`run.interrupted`. Build `Runtime` (stamp envelope, append to store,
fan out to sinks, yield). Per ADR-D5: relocate `runtime/sessions.py` (`SessionFactory`)
into `adapters/engines/openai_agents/` as its private execution store, and build
`adapters/stores/{memory,sqlite,redis}` as **new** event-log stores; `agents/mcp/` → `adapters/tools/mcp/` behind
`ToolSourcePort`; `runtime/observability.py` → `adapters/telemetry/langfuse/` as an
`EventSinkPort`. Extract SSE framing from `serve.py` into `adapters/protocols/sse/`;
rewrite handlers as thin Runtime calls. Shrink `App` to composition root + compat facade.
Move `BaseAgent`/`BaseWorkflow`/`CapabilitiesSpec` into `authoring/`, compiling to
`InvocableSpec`.

**Acceptance criteria**

- [ ] Contract-test suite passes identically against both real engines (LSP made executable)
- [ ] One `InvocableRegistry`; `agents/registry.py` and `workflows/registry.py` deleted
- [ ] Transcript-fidelity contract test (ADR-D5) passes on both engines: message-level transcript from engine execution state ≡ transcript from the event log, in content and order
- [ ] Crash-between-writes reconciliation (log written, engine state not) covered by an integration test; next turn replays the missing input into execution state
- [ ] `serve.py` handlers contain no engine- or shape-specific logic; SSE frames byte-identical to 1.2.1 (golden-file test)
- [ ] Langfuse traces now cover workflow runs too (proof of sink-based telemetry)
- [ ] Only `adapters/engines/openai_agents/` imports `agents`; only `adapters/engines/langgraph/` imports `langgraph` (linter-enforced)
- [ ] Compat facade: all README examples from 1.2.1 run unmodified

**Estimate:** L — the big one. **Risk:** highest of the epic; mitigate by landing engine
adapters behind a feature flag first, cutting serve over last. Do not split this story
across releases — a half-moved seam is worse than either endpoint.

*(Amendment 2026-08-05 — re-sequenced after Milestone 0, `milestone-0-findings.md`.)*
Milestone 0 (issues #52–#54, #56/#58/#59) already built a crude-but-real slice of this
story ahead of schedule: `EnginePort` for both engines, `Runtime` (stamp/append/fan-out/
yield, resume, pending), the memory+SQLite event-log stores, and a `/v2/...` chat +
`/pending`+`/resume` surface — all proven against real multi-agent/multi-turn (UC1),
interrupt/restart (UC2), and cancel-under-load (UC3) traffic with zero engine leakage
into any consumer (`milestone-0-findings.md` §2). **This retires the story's own
highest-risk bet** — that one `Runtime`/`EnginePort` abstraction could cover openai-agents
and LangGraph without a consumer-visible seam — as a measured fact, not a hope. The
estimate stays **L**, but the composition of that L changes:

- **Lower risk than originally scoped:** the acceptance criteria already met at spike
  quality are the transcript-fidelity test (both engines, `milestone-0-findings.md` §2
  falsifier 4) and the "no engine-specific logic in surfaces" criterion (§2 falsifier 1);
  Story 2 hardens these, it does not discover whether they are achievable.
- **Real remaining net-new scope, not yet touched by the spike:** Redis/Postgres event-log
  stores; `ToolSourcePort`/MCP relocation; `adapters/telemetry/langfuse/` as an
  `EventSinkPort`; the real `InvocableRegistry` replacing the hardcoded
  `dict[str, InvocableSpec]` every M0 test and the demo script built inline
  (`milestone-0-findings.md` §8); `App` actually becoming the composition root + compat
  facade — M0's `build_app` is a parallel `/v2/...` route, not wired into `App` and not
  byte-parity with v1's `serve.py`; and the crash-between-writes reconciliation test
  ADR-D5 requires, which M0 never exercised (no test in the M0 suite kills a process
  *between* the log write and the engine-state write — only between two fully-committed
  turns, per UC2's restart tests).
- **A hardening item M0 surfaced that this story must resolve, not just harden:** LangGraph
  durable checkpointers (`adapters/engines/langgraph/checkpointer.py`) cache per URL and
  bind to the event loop that first constructed them — fine for a server's one long-lived
  loop, a real constraint for anything else. Story 2's redis/postgres store work and this
  checkpointer behavior are coupled in a way the original story text didn't anticipate.
- **A test-infrastructure finding for Story 3, not Story 2:** `httpx.ASGITransport` cannot
  interleave a live control signal into an in-flight SSE response (it runs a request's
  whole ASGI call before returning any bytes) — Story 3's "pause honored at next safe
  point" acceptance criterion, when exercised over the real HTTP route rather than
  `Runtime` directly, needs a real ASGI server (e.g. `uvicorn` in a subprocess), not
  `ASGITransport`. Flagged here so it lands as a known requirement, not a mid-story
  surprise.

*(Amendment 2026-08-06 — #74 closed the composition-root half of that scope.)* `App` is
now a caller of one assembly seam (`agentdeck/composition.py`), and v1's chat endpoints are
served by the Runtime with the golden suite unchanged, so **"SSE frames byte-identical to
1.2.1" is met for `/agents/{name}/chat`** and the compat facade exists as a surface module.
What the criterion still lacks, and why:

- **`/workflows/*` is still on v1's runner.** The langgraph adapter takes text `Input` and
  reports its final state as `str(dict)`; v1's endpoints take an arbitrary JSON state and
  return the final state. Byte-parity there needs the adapter to carry a state-shaped input
  and a structured final state — engine work, ahead of the surface work.
- **Structured output has no canonical shape.** `RunCompleted.output` is `Input`, so an
  `output_type` agent's result travels as a namespaced `custom` event that the surface
  renders. With the workflow final state, that is the second recurrence — the promotion
  signal for a `DataBlock`/structured field, which is a schema PR, not a facade PR.
- **The Runtime's langgraph engine is not the configured one.** `v1_engines()` gives it an
  in-memory checkpointer, because resolving the settings checkpointer at `App.load()` would
  make the `[durability]` extra mandatory for chat-only installs. The workflow reroute has to
  resolve it, and that is where the M0 event-loop-binding constraint above will bite.
- **The event log is opt-in.** `AGENTDECK_EVENTS_BACKEND` defaults to `memory`, so the
  rerouted surface keeps a per-process log; a durable default needs a writable path, which
  `.agentdeck/` (mounted read-only) is not.

Deleting v1's runner glue remains the pre-stable gate's job: this PR rerouted, it deleted
nothing, and the glue is still what `App.chat` / `chat_stream` / the workflow endpoints use.

---

## Story 3 — Run control: pause / resume / cancel (Phase 3)

**As an** operator or calling application, **I want** to pause, resume, and cancel any
in-flight run by `run_id`, including from another process, **so that** long-running
agents and workflows are governable — and as proof that features now cost one
implementation instead of two.

**Scope.** `ControlPort` implementations (`memory`, `redis`); real `Gate` wired into
`RunContext`; gate checkpoints in the openai-agents adapter (between stream items,
before tool dispatch) and pause→interrupt mapping at node boundaries in the langgraph
adapter; status transitions recorded via the Story-1 state machine; four routes on serve
(`POST /runs/{id}/pause|resume|cancel`, `GET /runs/{id}`); the three control event kinds
flowing to all sinks. **Story 3b (same release):** build the `Gate` as a *mailbox* from
the start — `checkpoint()` drains queued input as well as signals at safe points — and
ship steering: `Runtime.send(run_id, Input, ctx)`, `POST /runs/{id}/messages`, and the
`input.appended` event written to the log **before** being drained into execution state
(ADR-D5 write ordering). Engines may declare `supports_steering=False` rather than fake it.

**Acceptance criteria**

- [ ] Contract tests: pause honored at next safe point; resume continues with full history; cancel raises cooperatively and emits `run.cancelled`; signals on terminal runs are no-ops — identical semantics across both engines
- [ ] Redis ControlPort: pause issued from process A stops a run executing in process B (integration test with two workers)
- [ ] Documented safe-point contract in the repo (`docs/`): what pause means, what resume replays, side-effect rules referencing `idempotency_key`
- [ ] `WAITING_HUMAN` vs `PAUSED` distinguished in status endpoint and events
- [ ] Approvals inbox (`/pending`) still works and now also lists operator-paused runs separately

**Estimate:** M. **Risk:** medium — the semantics are the work; the wiring is small.
Depends on Stories 1–2.

*(Amendment 2026-08-05 — re-grounded after Milestone 0, `milestone-0-findings.md`.)* M0's
UC3 already shipped a cancel-only slice of this story (`ControlPort`, `Gate`,
memory+SQLite adapters, cross-process cancel proven via a real subprocess) — Story 3
extends `Signal` to add `PAUSE`/`RESUME` and steering rather than building control from
zero. Two findings raise the estimate's risk, not its size: the double-resume guard
built in M0 is an `asyncio.Lock` keyed by `run_id` **inside one `Runtime` instance** —
correct for two callers racing one process, silently wrong for two processes racing the
same run through two separate `Runtime`s over one store, which is exactly the shape the
"Redis ControlPort: pause from process A stops a run in process B" acceptance criterion
above requires. A cross-process-safe resume needs a compare-and-set primitive on
`SessionStorePort`, which the frozen ports don't have yet — this is coupled work, not two
independent line items, and should be scoped together rather than discovered mid-story.
Separately, the "pause honored at next safe point" criterion needs a real ASGI server to
test over the actual HTTP route (`httpx.ASGITransport` cannot interleave a live signal
into an in-flight SSE response, per the Story 2 amendment above) — plan the test
infrastructure for that up front instead of learning it while red.

---

## Story 4 — Caller-injected capabilities (Phase 4)

**As a** surface author, **I want** filesystem, terminal, and approval capabilities to be
ports supplied by the caller on `RunContext`, with the sandbox as the default
implementation, **so that** the same agent can run against the sandbox over HTTP and
against an editor's workspace over ACP — unlocking every UI/editor protocol.

**Scope.** `CapabilityProvider` on `RunContext` with `require()` raising
`CapabilityUnavailable`; wrap existing `Workspace`/`SandboxSession` as
`adapters/caps/sandbox/` (`FilesystemPort`, `TerminalPort`); dissolve `BaseSandboxAgent`
into `BaseAgent` + `CapabilitiesSpec` (deprecated alias retained); engine adapter chooses
the SDK agent class from the compiled `CapabilityRequest`; re-target `SkillExecutor` to
consume the ports instead of the ambient ContextVar workspace; move the Chat-Completions
shims from `capabilities/{compaction,filesystem}.py` into the openai-agents adapter.

**Acceptance criteria**

- [ ] An agent declaring `shell=True` runs identically before/after (regression suite), with the sandbox now injected rather than ambient
- [ ] The same agent runs with a test double `FilesystemPort` and never touches the sandbox (unit-level proof of substitutability)
- [ ] `SkillExecutor` has no import of `Workspace`; skills pass existing tests against the sandbox port
- [ ] `BaseSandboxAgent` emits a deprecation warning but works; README/docs updated
- [ ] Missing capability produces a clear build/run-time error naming the port and the surface that failed to provide it

**Estimate:** M. **Risk:** medium — the ContextVar-to-injection change touches skills;
land it behind the compat alias. Depends on Story 2; independent of Story 3.

---

## Story 5 — ACP surface (Phase 5)

**As a** user of an ACP-capable editor (Zed, JetBrains, …), **I want** to run any
agentdeck agent inside my editor via `agentdeck acp`, **so that** AgentDeck demonstrably
"speaks every protocol" — with the editor owning files and permissions.

**Scope.** `adapters/protocols/acp/`: JSON-RPC 2.0 stdio framing, method dispatch for
`initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`; the
event→`session/update` mapper (single churn-absorbing file, protocol version pinned);
client-backed `FilesystemPort` / `TerminalPort` / `ApprovalPort` that round-trip
`fs/read_text_file`, terminal methods, and `session/request_permission` over the pipe.
`surfaces/acp/` entrypoint registered as `agentdeck acp` console script. Capability
negotiation: client-declared capabilities at `initialize` decide which ports enter
`ctx.caps`, sandbox fallback otherwise; advertised agent capabilities derived from the
registry, not hardcoded.

**Acceptance criteria**

- [ ] `session/prompt` streams `agent_message_chunk` updates and terminates correctly for both an agent and a workflow invocable
- [ ] `session/load` replays history from the event log as `session/update` notifications in `seq` order
- [ ] `session/cancel` maps to `Runtime.signal(CANCEL)` and the in-flight prompt stops at the next safe point (reuses Story 3)
- [ ] An agent reading a file receives editor-buffer content via the client filesystem port, not sandbox content (integration test with a scripted fake client)
- [ ] Permission request surfaces as `session/request_permission` and the decision resumes the run (reuses interrupt machinery)
- [ ] Zero changes required in `core/`, engines, or `surfaces/serve/` to land this story (the architecture's scoreboard claim, verified by diff)

**Estimate:** M. **Risk:** medium-low internally; external risk is ACP spec churn —
contained by the pinned version and single mapper file. Depends on Stories 3 and 4.

---

## Sequencing and dependency graph

```text
Story 1 ──▶ Story 2 ──▶ Story 3 ──▶ Story 5
                   └──▶ Story 4 ──▶ Story 5
```

Stories 3 and 4 can proceed in parallel after Story 2. Recommended order of merge:
1 → 2 → 3 → 4 → 5. The epic's demo at the end: one agent, unmodified, running (a) over
SSE with sandbox capabilities, (b) paused and resumed from a second process, and
(c) inside an ACP editor reading the editor's unsaved buffer — three surfaces, one event
log, zero agent-code changes.
