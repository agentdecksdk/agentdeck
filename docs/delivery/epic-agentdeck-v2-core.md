# Epic: AgentDeck v2 — Unified Core & Runtime

**Closed.** Restructure agentdeck from two parallel silos (agents / workflows) into three rings: a
zero-I/O core, engine adapters behind one lifecycle boundary, and thin surfaces rendering one
canonical event stream. **Reference:** `agentdeck-v2-architecture.md` · **Baseline:** agentdeck
1.2.1 (`60b95b6`). Every checkbox below is as the epic was left, not as the tree stands.

**Epic-level definition of done**

- [ ] `make test` green after every story (each story is independently shippable)
- [ ] Existing public API (`App`, `run_agent`, `run_workflow`, `chat`, `chat_stream`) works unchanged via the compat facade
- [ ] Existing SSE wire format byte-preserved; existing `.agentdeck/` projects load with zero edits
- [ ] `import-linter` CI gate: `core/` imports nothing from `agents`, `langgraph`, `fastapi`, `redis`
- [ ] Contract-test suite runs against both engines and passes identically
- [ ] CHANGELOG entries per story; design doc updated where implementation diverged

**Out of scope:** AG-UI/A2A adapters, dashboard, stdlib/toolkit content (Appendix A), auth beyond
`Principal` on context, multi-tenant deployment — follow-up epics.

## Stories

| # | Story (phase) | The ask | Scope | Estimate · risk |
|---|---|---|---|---|
| 1 | Core nouns: events, RunContext, ports (1) | The canonical schema, `RunContext` and port ABCs exist and thread through existing paths, changing no behavior | `core/` (`events.py`, `content.py`, `context.py`, `invocable.py`, `status.py`, `errors.py`, `ports/*`) with no-op ports; `Input = list[ContentBlock]` plus a str→TextBlock shim; a default `RunContext` through `App.run_agent`/`run_workflow`/`chat` and both runner chains; the contract-suite skeleton on a stub engine | S–M · low, additive; everything else depends on it |
| 2 | The seam: EnginePort, Runtime, adapters, serve rewrite (2) | Both SDK integrations move behind `EnginePort` under one `Runtime`, ending the agent/workflow bifurcation | `HeadlessRunner` → `adapters/engines/openai_agents/`; `workflows/*` + `runtime/checkpointer.py` → `adapters/engines/langgraph/`, mapping `astream`/`interrupt` onto `node.updated`/`run.interrupted`; `Runtime` (stamp, append, fan out, yield). Per ADR-D5 `runtime/sessions.py` becomes the openai-agents adapter's private execution store and `adapters/stores/{memory,sqlite,redis}` are **new** event logs. `agents/mcp/` → `adapters/tools/mcp/` behind `ToolSourcePort`, `runtime/observability.py` → a Langfuse `EventSinkPort`, SSE framing → `adapters/protocols/sse/`; `App` shrinks to composition root + compat facade, `BaseAgent`/`BaseWorkflow`/`CapabilitiesSpec` move to `authoring/` | L · highest of the epic; adapters behind a flag first, serve last, never split across releases |
| 3 | Run control: pause / resume / cancel (3) | Any in-flight run is governable by `run_id`, including from another process | `ControlPort` (`memory`, `redis`); a real `Gate` on `RunContext`; gate checkpoints in the openai-agents adapter (between stream items, before tool dispatch) and pause→interrupt at langgraph node boundaries; status through the Story-1 machine; `POST /runs/{id}/pause\|resume\|cancel` + `GET /runs/{id}`; the three control kinds to all sinks. **Story 3b (same release):** the `Gate` is a *mailbox* from the start — `checkpoint()` drains queued input as well as signals — shipping `Runtime.send(run_id, Input, ctx)`, `POST /runs/{id}/messages`, and `input.appended` written to the log **before** draining into execution state (ADR-D5 write ordering); an engine may declare `supports_steering=False` rather than fake it | M · medium — the semantics are the work, the wiring is small |
| 4 | Caller-injected capabilities (4) | Filesystem, terminal and approval become ports the caller supplies on `RunContext`, sandbox as default | `CapabilityProvider` on `RunContext`, `require()` raising `CapabilityUnavailable`; `Workspace`/`SandboxSession` wrapped as `adapters/caps/sandbox/` (`FilesystemPort`, `TerminalPort`); `BaseSandboxAgent` dissolved into `BaseAgent` + `CapabilitiesSpec` (deprecated alias retained); the engine adapter picks its SDK agent class from the compiled `CapabilityRequest`; `SkillExecutor` onto the ports instead of the ambient ContextVar workspace; the Chat-Completions shims from `capabilities/{compaction,filesystem}.py` into the openai-agents adapter | M · medium — ContextVar-to-injection touches skills; land behind the compat alias |
| 5 | ACP surface (5) | Any agentdeck agent runs inside an ACP editor via `agentdeck acp`, the editor owning files and permissions | `adapters/protocols/acp/`: JSON-RPC 2.0 stdio framing; dispatch for `initialize`, `session/new`, `session/load`, `session/prompt`, `session/cancel`; the event→`session/update` mapper as one churn-absorbing file with the protocol version pinned; client-backed `FilesystemPort`/`TerminalPort`/`ApprovalPort` round-tripping `fs/read_text_file`, terminal methods and `session/request_permission`; `surfaces/acp/` as the `agentdeck acp` console script; capabilities the client declares at `initialize` decide which ports enter `ctx.caps`, sandbox otherwise | M · medium-low internally; external risk is ACP spec churn |

```text
Story 1 ──▶ Story 2 ──▶ Story 3 ──▶ Story 5
                   └──▶ Story 4 ──▶ Story 5
```

Closing demo: one unmodified agent, running over SSE with sandbox capabilities, paused and resumed
from a second process, and inside an ACP editor reading the editor's unsaved buffer.

## Acceptance criteria

| story | criterion | met |
|---|---|---|
| 1 | Event union with envelope (`v`, `seq`, `run_id`, `session_id`, `tenant`, `ts`, `kind`) and all §4.2 kinds, with round-trip and unknown-kind-tolerance tests | [ ] |
| 1 | `RunContext` required on every internal run path; the public API constructs a default so user code is unaffected | [ ] |
| 1 | Run status machine in `core/status.py`, with tests proving terminal-state signals are no-ops | [ ] |
| 1 | import-linter contract for `core/` active in CI | [ ] |
| 1 | No behavior change: the full existing test suite passes untouched | [ ] |
| 2 | Contract-test suite passes identically against both real engines (LSP made executable) | [ ] |
| 2 | One `InvocableRegistry`; `agents/registry.py` and `workflows/registry.py` deleted | [ ] |
| 2 | Transcript-fidelity contract test (ADR-D5) on both engines: transcript from engine execution state ≡ transcript from the event log, in content and order | [ ] |
| 2 | Crash-between-writes reconciliation (log written, engine state not) covered by an integration test; the next turn replays the missing input into execution state | [ ] |
| 2 | `serve.py` handlers hold no engine- or shape-specific logic; SSE frames byte-identical to 1.2.1 (golden-file test) | [ ] |
| 2 | Langfuse traces cover workflow runs too (proof of sink-based telemetry) | [ ] |
| 2 | Only `adapters/engines/openai_agents/` imports `agents`; only `adapters/engines/langgraph/` imports `langgraph` (linter-enforced) | [ ] |
| 2 | Compat facade: all README examples from 1.2.1 run unmodified | [ ] |
| 3 | Contract tests: pause honored at next safe point; resume continues with full history; cancel raises cooperatively and emits `run.cancelled`; terminal-run signals are no-ops — identical across both engines *(#45: `tests/contract/test_control.py`, parametrized over the stub and openai-agents engines. The langgraph engine makes no gate checkpoint, so a workflow run has no safe point yet — #128, split out because deciding what resuming a checkpointed graph replays is its own slice.)* | [x] |
| 3 | Redis ControlPort: pause from process A stops a run in process B (two-worker integration test) *(#45 shipped the cross-process path over the SQLite control port, proven by `test_uc3_cross_process_cancel`; a Redis control port is still unbuilt.)* | [ ] |
| 3 | Documented safe-point contract in the repo: what pause means, what resume replays, side-effect rules referencing `idempotency_key` *(#45: `docs-site/content/concepts/run-control.mdx`, carrying #85's cancel-latency bound.)* | [x] |
| 3 | `WAITING_HUMAN` vs `PAUSED` distinguished in events, and in `can_resume`'s two resume shapes — a value, or nothing *(#45. A status *endpoint* is #116's.)* | [x] |
| 3 | Approvals inbox (`/pending`) still works and lists operator-paused runs separately *(#45 left `pending()` on `WAITING_HUMAN` alone — an operator's pause is not an approval awaiting an answer, and no "Done when" in #45 asked for the listing.)* | [ ] |
| 4 | An agent declaring `shell=True` runs identically before/after (regression suite), with the sandbox injected rather than ambient | [ ] |
| 4 | The same agent runs against a test-double `FilesystemPort` and never touches the sandbox | [ ] |
| 4 | `SkillExecutor` has no import of `Workspace`; skills pass existing tests against the sandbox port | [ ] |
| 4 | `BaseSandboxAgent` emits a deprecation warning but works; README/docs updated | [ ] |
| 4 | A missing capability produces a clear error naming the port and the surface that failed to provide it | [ ] |
| 5 | `session/prompt` streams `agent_message_chunk` updates and terminates correctly for both an agent and a workflow invocable | [ ] |
| 5 | `session/load` replays history from the event log as `session/update` notifications in `seq` order | [ ] |
| 5 | `session/cancel` maps to `Runtime.signal(CANCEL)` and stops the in-flight prompt at the next safe point (reuses Story 3) | [ ] |
| 5 | An agent reading a file receives editor-buffer content via the client filesystem port, not sandbox content (scripted fake client) | [ ] |
| 5 | A permission request surfaces as `session/request_permission` and the decision resumes the run (reuses interrupt machinery) | [ ] |
| 5 | Zero changes in `core/`, engines or `surfaces/serve/` to land this story — the architecture's scoreboard claim, verified by diff | [ ] |

## Amendments

*(2026-08-05 — Story 2 re-sequenced after Milestone 0, `milestone-0-findings.md`.)* M0 (#52–#54,
#56/#58/#59) built a crude slice of Story 2 ahead of schedule — `EnginePort` on both engines,
`Runtime`, memory+SQLite event logs, a `/v2/...` chat + `/pending`+`/resume` surface — proven against
real handoff (UC1), interrupt/restart (UC2) and cancel-under-load (UC3) traffic with zero engine
leakage into any consumer, which **retires the story's highest-risk bet as a measured fact**. The
estimate stays **L**; its composition changes:

| | |
|---|---|
| Met at spike quality — hardened, not discovered | Transcript fidelity on both engines, and no engine-specific logic in surfaces (`milestone-0-findings.md` §2, falsifiers 4 and 1) |
| Net-new, untouched by the spike | Redis/Postgres event logs; `ToolSourcePort`/MCP relocation; the Langfuse `EventSinkPort`; the real `InvocableRegistry` replacing M0's inline `dict[str, InvocableSpec]` (§8); `App` actually becoming composition root + compat facade, since M0's `build_app` is a parallel `/v2/...` route with no byte-parity; and ADR-D5's crash-between-writes reconciliation test — M0 kills a process only between two committed turns, never between the log write and the engine-state write |
| To resolve, not just harden | LangGraph durable checkpointers cache per URL and bind to the event loop that first built them, coupling this story's store work to checkpointer behavior. *(Resolved 2026-08-06, #75: savers cache per event loop, and the Redis and Postgres event logs landed with them, wired to `AGENTDECK_EVENTS_BACKEND` through #74's `resolve_event_store`. The coupling was real but shallow — one PR, not one design. Ceiling recorded in the code: the per-loop cache never frees a finished loop's saver, so a process running many loops accumulates a connection each; no effect on a server.)* |
| Story 3's problem, found here | `httpx.ASGITransport` runs a request's whole ASGI call before returning bytes, so it cannot interleave a live control signal into an in-flight SSE response. Testing "pause honored at next safe point" over the real HTTP route needs `uvicorn` in a subprocess |

*(2026-08-05 — Story 3 re-grounded after Milestone 0.)* M0's UC3 shipped a cancel-only slice, so
Story 3 extends `Signal` with `PAUSE`/`RESUME` and steering rather than building control from zero.
Two findings raise risk, not size. M0's double-resume guard is an `asyncio.Lock` keyed by `run_id`
inside one `Runtime` — correct for two callers racing one process, silently wrong for two processes
racing one run through two `Runtime`s over one store, which is exactly the shape the Redis-ControlPort
criterion requires; a cross-process-safe resume needs a compare-and-set primitive the frozen
`SessionStorePort` lacks, so scope the two together. And "pause honored at next safe point" needs a
real ASGI server per the amendment above — plan that test infrastructure up front, not while red.

*(2026-08-06 — #74 closed the composition-root half of Story 2's scope.)* `App` is now a caller of
one assembly seam (`agentdeck/composition.py`) and v1's chat endpoints are served by the Runtime with
the golden suite unchanged, so **"SSE frames byte-identical to 1.2.1" is met for
`/agents/{name}/chat`** and the compat facade exists as a surface module. Deleting v1's runner glue
stays the pre-stable gate's job: that PR rerouted and deleted nothing. What the criterion still lacks:

| gap | why |
|---|---|
| `/workflows/*` is still on v1's runner | The langgraph adapter takes text `Input` and reports final state as `str(dict)`; v1's endpoints take arbitrary JSON state and return it. Byte-parity needs a state-shaped input and a structured final state — engine work ahead of surface work |
| Structured output has no canonical shape | `RunCompleted.output` is `Input`, so an `output_type` result travels as a namespaced `custom` event. With the workflow final state that is the second recurrence — the promotion signal for a `DataBlock`, which is a schema PR |
| The Runtime's langgraph engine is not the configured one | `v1_engines()` hands it an in-memory checkpointer, because resolving the settings checkpointer at `App.load()` would make `[durability]` mandatory for chat-only installs. The workflow reroute must resolve it, where the event-loop binding bites |
| The event log is opt-in | `AGENTDECK_EVENTS_BACKEND` defaults to `memory`, so the rerouted surface keeps a per-process log; a durable default needs a writable path, which read-only `.agentdeck/` is not |
