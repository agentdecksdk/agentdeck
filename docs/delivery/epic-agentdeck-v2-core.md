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
