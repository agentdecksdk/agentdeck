# Changelog

All notable changes to agentdeck. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Entries are user-facing — what changed for
someone using the package, in `Added / Changed / Deprecated / Removed /
Fixed / Security` order — and are written to be attached to a release as-is.

## [Unreleased]

### Added
- `InvocableRegistry` (`agentdeck.runtime.discovery`): the v2 Runtime's list of
  what it can run is now discovered from your `./.agentdeck/` project instead of
  written out by hand at every entry point. `InvocableRegistry(engines).load()`
  reads the same bundles v1 always has — `agents/<bundle>/agent.py`,
  `workflows/<bundle>/workflow.py` — and returns the name-to-invocable mapping
  `Runtime` takes, with each bundle pointed at the engine its shape belongs to.
  Adding an agent or a workflow to a project no longer means editing wiring code.
  An agent and a workflow claiming one name, and a project whose bundles need an
  engine the Runtime wasn't given, both fail at load with a message naming the
  offender, rather than at the moment somebody runs it. (Two bundles of the same
  kind exporting one class name still collapse to a single invocable, as in v1.)
  Skills are not discovered as invocables yet — no engine runs a `SKILL.md`
  bundle. v1's `App` and its discovery are unchanged.
- `ToolSourcePort` (`agentdeck.core.ports`): tools now arrive from a source
  behind one small interface — `resolve(spec)` hands back a `ToolSet` of the
  tools an invocable gets, the names of the ones it asked for and did not get,
  and the notice to put in front of the model when something is missing. MCP is
  the first source, and its behavior is unchanged: an unconfigured or
  unreachable server still degrades a run instead of failing it, and an agent
  whose servers are all up gets its instructions back byte-for-byte, so upstream
  prompt caches keep hitting.

### Changed
- MCP now lives in `agentdeck.adapters.tools.mcp` (registry, hardened HTTP
  transport, agent wiring — all unchanged). `from agentdeck.agents.mcp import ...`,
  `from agentdeck.agents.mcp.lifecycle import ...` and `from agentdeck.agents
  import ...` keep working and hand back the same objects; both paths will be
  dropped in a later release. The deeper module paths
  `agentdeck.agents.mcp.transport` and `agentdeck.agents.mcp.wiring` are gone —
  import those names from the package instead.

## [2.0.0b3] - 2026-08-05

A hardening release: no new surface, sturdier runtime. Cancel a run from
another process, answer an approval from either of two servers without the
workflow running twice, and keep telemetry from growing memory or losing
events behind a wedged endpoint. Every guarantee here is enforced by the
event store itself rather than by in-process locks, so it holds when a
second worker joins. The v1 public surface remains byte-for-byte unchanged.

### Added
- Run control (`agentdeck.core.ports.control`, `agentdeck.adapters.control`): a
  `ControlPort` for cross-process cancel signals, backed by an in-memory adapter
  for dev/tests and a SQLite-backed one durable enough for a second OS process to
  reach a run it never held a reference to. The OpenAI Agents engine checks a
  cooperative gate between stream items and stops cleanly on cancel, emitting a
  single `run.cancelled` and leaving a truncated-but-coherent replay behind (no
  `message.completed` for the interrupted message). New `agentdeck runs signal
  <run_id> cancel --control-db <path>` CLI command to send that signal from a
  second terminal.
- `EventStorePort` (not yet part of any stable public API) gains focused
  queries alongside its whole-log reads: `last_seq` (a run's highest
  recorded `seq`), `run_status` (one run's status, derived from its own
  events), `list_runs` (every run for a tenant, optionally filtered by
  status), and pagination (`offset`/`limit`) on `read`. Both the memory and
  SQLite stores implement all four; the SQLite ones use the existing
  run/log indexes.
- `EventStorePort.claim_resume` (not yet part of any stable public API): a
  conditional append that records `run.resumed` only if the run is still waiting
  on a human answer *and* the event's `seq` is still the run's next one, as one
  indivisible step, and reports whether it won. The memory store gets that for
  free; the SQLite store does it in a single `BEGIN IMMEDIATE` transaction, so
  the events file itself picks the winner.

### Changed
- Internal: the v2 event-log port (not yet part of any stable public API) is
  now named `EventStorePort` instead of `SessionStorePort`, to avoid confusion
  with the OpenAI Agents engine's own session-scoped storage. No behavior
  change and nothing outside the package imports this port.
- Internal: the Runtime's resume path and the `/pending` listing now use
  `EventStorePort`'s focused queries instead of folding a whole log to answer
  one run's status or find waiting runs. Same results, much less work per call:
  a resume deserializes only its own run's events instead of the whole session's
  (22 instead of 4,400 on a 200-run session), and the pending listing is one
  indexed statement returning each run's last lifecycle event — one event parsed
  per run instead of every event of every log (4.2 ms instead of 32 ms for the
  same 201 runs).
- Event sinks are now fed from a bounded queue with one worker each, instead of a
  fresh task per event per sink. A wedged sink (telemetry endpoint down, audit
  store backpressured) now costs a fixed backlog and one task rather than growing
  memory for as long as the process runs. A run still never waits on a sink: when a
  sink's queue is full its stalest event is dropped rather than the run delayed — but
  only once the sink has been given a turn to catch up, so a sink that is keeping up
  loses nothing however fast the run produces events. Dropped events and failed
  emits are counted per sink and reported in the logs — never discarded silently, and
  never one stack trace per event — and a sink that raises five times in a row is
  disabled instead of being retried for the rest of the process's life. Two side
  effects worth knowing: each sink's `emit` is now called one event at a time in
  submission order and is never re-entered, and `Runtime.drain()` flushes the queues
  and stops the workers. Sinks remain a lossy tap by design; a consumer that must
  see every event reads the event store, which is the complete copy.

### Removed
- `EventStorePort.list_log_keys` (not yet part of any stable public API), along
  with the log-by-log pending scan that was its only caller. `list_runs` answers
  the same question without enumerating logs first.

### Fixed
- Duplicate-resume protection now holds between processes, not just between tasks
  in one process. Two servers (or a server and a second tool) sharing one SQLite
  event store can answer the same interrupt at the same instant and exactly one of
  them resumes the run; the other is a clean no-op, not an error. Previously the
  guard was a process-local lock, so each process could claim the same waiting run
  — running the workflow's next node twice and writing two `run.resumed` events
  with the same `seq`. A claim that was slow enough to miss a whole
  interrupt-resume-interrupt round of its run now loses too, instead of answering
  the run's *second* question with the first one's value.
- The OpenAI Agents engine no longer runs the SDK's default trace exporter on
  keyless/fake-model runs (tests, CI, the M0 demo): it now passes a `RunConfig`
  with tracing disabled unless `AGENTDECK_OPENAI_AGENTS_TRACING_ENABLED=true` is
  set, so a bare checkout no longer logs a non-fatal `Tracing client error 401`
  or attempts an unsanctioned outbound HTTPS call.

## [2.0.0b2] - 2026-08-05

Second beta of the v2 line: both real engines now run behind the
engine-agnostic core — the same event stream, store, and surfaces serve
OpenAI Agents chats and LangGraph workflows alike. The v1 public surface
remains byte-for-byte unchanged.

### Added
- LangGraph engine adapter (`agentdeck.adapters.engines.langgraph`): runs graph
  workflows behind `EnginePort`, surviving process restarts — an interrupted run's
  status and resume point persist, and resuming continues the same event sequence
  with no duplicates. A new `GET /pending` / `POST /resume` surface lists and
  answers interrupted runs; resuming the same run twice at once resolves exactly
  once, and resuming a run that already finished is a no-op rather than an error.
- OpenAI Agents engine adapter (`agentdeck.adapters.engines.openai_agents`):
  runs a pre-built `agents.Agent` — handoffs and tools included — behind
  `EnginePort`, streaming the canonical events (`text.delta`,
  `message.completed`, `tool.call.started`/`.completed`, and a namespaced
  `custom` event per handoff). SDK session keys are tenant-scoped, so two
  tenants reusing the same session id never share a conversation.
- SQLite event store (`agentdeck.adapters.stores.sqlite`): a durable event
  log with the same append-only, per-run-`seq` contract as the in-memory
  store.
- Minimal v2 surfaces (`agentdeck.surfaces.serve`, `agentdeck.surfaces.cli`):
  an SSE route and a compact CLI chat renderer for v2 runs — speakers are
  distinguished by `origin` + `message_id` alone, and transcripts rebuild
  from `message.completed` events without delta assembly. The v1 server is
  untouched.
- Docs site: a Concepts section (agents, capabilities, skills, workflows)
  written against the shipped v1 surface, and the AgentDeck brand — palette,
  type scale, self-hosted fonts so the exported site makes no external
  requests.

### Removed
- Docs site: the empty Guides and Examples sections; each returns when its
  first real page exists.

## [2.0.0b1] - 2026-08-05

First beta of the v2 line: the engine-agnostic core and the Runtime land
alongside the shipped v1 harness. **The v1 public surface is unchanged** —
`App`, `run_agent`, `run_workflow`, `chat`, `chat_stream`, the `./.agentdeck/`
project layout and the SSE wire format are byte-for-byte what 1.2.1 served.
The bump marks the start of the v2 rebuild, not a break in what already works.

### Added
- `agentdeck.core`: canonical event schema v1 — a closed eight-field `Event`
  envelope over payload classes discriminated by `kind`, `parse_event()`
  tolerating unknown kinds and fields, content blocks, and the
  `check_contiguous` / `check_terminal` ordering invariants. Nothing imports
  it yet; v1 runtime behavior is unchanged.
- `agentdeck.core`: `RunContext` (the run's identity and limits, passed to
  every port), `InvocableSpec` / `InvocableKind` (one noun for agents,
  workflows and skills), and the first three ports — `EnginePort`,
  `SessionStorePort`, `EventSinkPort`.
- `agentdeck.runtime.service.Runtime`: the v2 run loop — stamp the envelope,
  append to the log, fan out to sinks, yield, in that order, so an event a
  consumer has seen is already persisted. Every run is closed in the log: an
  engine that raises or stops early gets `run.failed`, an abandoned stream
  gets `run.cancelled`, and nothing follows a terminal event. Sinks never
  stall a run; `Runtime.drain()` flushes in-flight emits at shutdown.
- In-memory event store (`agentdeck.adapters.stores.memory`) and scripted
  stub engine (`agentdeck.adapters.engines.stub`) — the stub is the reference
  implementation of the engine contract.
- Cross-engine contract test suite (`tests/contract/`): first event at
  `seq` 0, contiguous `seq`, exactly one terminal event and it is last,
  persist-before-yield. Every engine added later inherits it.
- Golden wire baselines (`tests/golden/`): byte-level snapshots of the v1
  HTTP/SSE surface, replayed on every test run; re-recorded only
  deliberately via `make golden`.
- Import-linter contracts wired into `make check` / CI, enforcing the
  architecture's import boundaries.
- Docs site: working search (a Pagefind index built at export, guarded by a
  CI check) and anti-rot tests — published Python samples are parsed and
  their imports resolved, links must resolve, and navigation must match the
  pages.

### Fixed
- Docs site: Getting Started installs from a git tag and documents provider
  configuration instead of describing a contributor clone; the overview's
  examples run as printed; `.env.example` no longer claims a legacy default
  for `OPENAI_BASE_URL` (empty means the SDK default).

## [1.2.1] - 2026-08-03

No changes to the `agentdeck` package itself — this version covers the
documentation platform and its CI.

### Added
- `docs-site/`: MDX documentation platform (Nextra 4, Next.js App Router),
  statically exported to GitHub Pages under `/agentdeck` — deployed on
  release, build-checked on every PR that touches it.

### Fixed
- Docs build no longer fails to prerender (zod pinned to 4.3.5 via
  `overrides`, lockfile committed, workflows install with `npm ci`).
- "Edit this page" links point at `dev` instead of a feature branch.

## [1.2.0] - 2026-07-28

### Added
- `AGENTDECK_LANGFUSE_BASE_URL`: Langfuse 4.x endpoint name; wins over
  `AGENTDECK_LANGFUSE_HOST` (kept as the legacy alias) and is mirrored to
  sandboxed skills as both `LANGFUSE_BASE_URL` and `LANGFUSE_HOST`.

## [1.1.0] - 2026-07-27

### Added
- `BaseAgent.handoffs` entries may be a `str` registry name, resolved lazily
  at `build()` time — two agents that hand off to each other no longer need
  to import each other's module. Unknown names raise `NotFoundError` naming
  the available agents; mutual handoffs resolve without recursing forever.
- Durable timer waits: `agentdeck.workflows.sleep_until(when)` pauses a
  `durable = True` workflow node until a timezone-aware wall-clock moment.
  `App.due_resumes()` lists timer threads whose wake time has passed;
  `App.tick()` resumes every due thread. Callers own the scheduling cadence
  (cron, systemd timer, a loop) — agentdeck runs no daemon. Naive datetimes
  are rejected with a clear `ValueError`.

### Fixed
- `App.chat(..., session_id=...)` turns now carry that session id on the
  root Langfuse trace instead of always tracing with a null session —
  per-customer trace grouping was silently broken.

## [1.0.0] - 2026-07-27

### Added
- Human-in-the-loop for `durable = True` workflows: a node calling
  `agentdeck.workflows.interrupt(payload)` pauses the run; `run_workflow`
  returns `{"type": "interrupt", "payload": ..., "thread_id": ...}` instead
  of a final state. `App.resume_workflow(name, thread_id, value)` answers it;
  `App.pending_interrupts()` lists every thread still waiting. Same trio on
  `BaseWorkflow` as `run` / `resume` / `pending`.
- `GET /workflows/{name}/pending` and
  `POST /workflows/{name}/{thread_id}/resume`; `POST /workflows/{name}`
  takes an optional `thread_id` query parameter so durable runs can start
  over HTTP.
- `App.run_workflow_stream(name, state=None, thread_id=None)`: async
  iterator yielding a `node_update` event per completed node, a `custom`
  event per stream-writer call, then one terminal `done` event with the
  final state. A paused run ends with an `interrupt` event in place of
  `done`, over HTTP too (`POST /workflows/{name}?stream=true`).
- `AgentNode` forwards its nested agent's text deltas into the workflow's
  custom stream, so a workflow-driven chat streams tokens the same as a
  direct agent chat.
- `subagents = [...]` on `BaseAgent`: an opt-in `spawn_subagent` tool that
  lets the model delegate a one-shot task to another registered agent
  (isolated run, no shared history, depth-limited). Disallowed or nested
  spawns return an `error: ...` string instead of raising.

### Changed
- **Breaking:** `.agentdeck/` project layout now uses top-level type
  subdirectories — `agents/<bundle>/agent.py` and
  `workflows/<bundle>/workflow.py`. `skills/*/SKILL.md` is unchanged. No
  migration shim: an old-layout project raises `ConfigError` pointing at the
  new paths instead of silently discovering nothing.
- A non-durable workflow whose node calls `interrupt()` raises `ConfigError`
  instead of silently returning an unresumable state.

### Fixed
- Building a `durable=True` sqlite workflow from sync code no longer raises
  `RuntimeError: no running event loop`.

## [0.2.0] - 2026-07-26

### Added
- `App.chat_stream(name, session_id, message)`: async iterator of text
  deltas with a terminal `StreamDone(final_output, usage)`, same session
  semantics as `chat()`; the run is cancelled cleanly when the iterator is
  closed or abandoned.
- `POST /agents/{name}/chat?stream=true`: `text/event-stream` response with
  incremental `delta` events and a final `done` event; mid-stream failures
  emit an `error` event; invalid requests are rejected with 422 before the
  stream starts. Sent with anti-buffering headers for proxies.
- `agentdeck/errors.py`: one exception hierarchy — `AgentdeckError` base,
  `NotFoundError`, `SkillError` (with `SkillExecutionError`, `SkillEnvError`),
  `ConfigError`.
- `App.open()` async context manager and idempotent `App.aclose()`;
  `agentdeck-serve` wires them through a FastAPI lifespan so SIGTERM shuts
  down Redis and MCP servers cleanly.
- `App(session_factory=...)` DI seam for tests.
- Workflow durability: `BaseWorkflow.durable = True` compiles the graph with
  a checkpointer from the new `AGENTDECK_CHECKPOINT_*` settings (`sqlite` |
  `postgres` | `memory`); `run_workflow` / `BaseWorkflow.run` accept
  `thread_id` so a run can resume, including across a real process restart.
  New optional `[durability]` extra with a clear `ImportError` when missing.

### Changed
- The streamed `done` event's `"output"` is the SDK's `final_output`
  (matching non-streamed `chat()`), not re-joined text deltas.
- `agentdeck-serve` answers `503` before startup completes instead of
  raising; `NotFoundError` maps to 404; other errors return a fixed 500 body
  with the detail logged server-side instead of echoed to the client.
- Registries raise `NotFoundError` instead of bare `KeyError`; invalid
  configuration raises `ConfigError` instead of `ValueError`; skill failures
  raise `SkillExecutionError` / `SkillError` instead of bare `RuntimeError`.

## [0.1.0] - 2026-07-26

### Added
- `App` single entry point: discovers and builds agents / workflows / skills
  from the `./.agentdeck` project dir; `run_agent`, `run_workflow`, `chat`
  (session memory via Redis or in-process SQLite fallback), `session_for`.
- `agentdeck-serve` FastAPI surface: `/health`, `/agents/{name}/chat`,
  `/workflows/{name}` (`[serve]` extra).
- `web_search` function tool (Tavily-backed, model-agnostic).
- `runtime/capture.py`: the `Capture` / `CaptureActor` / `CAPTURE_ENV`
  host↔sandbox wire contract.
- Packaging: pyproject with `serve` / `dev` / `observability` extras,
  Makefile, Dockerfile + compose (app + Redis), `.env.example`, pre-commit,
  CI + tag-driven release workflow.

### Changed
- Extracted from SysAgentsHarness and renamed: package `sysagent` →
  `agentdeck`, env prefixes `SYSAGENT_*` → `AGENTDECK_*`.
- Neutralized donor defaults (private endpoints, model, MCP hosts); empty
  `OPENAI_BASE_URL` means the SDK default.
- Pinned `openai==2.32.0` to match `openai-agents==0.17.0` (2.33+ crashes
  the run loop).
- `BaseAgent.run()` is a one-shot headless run.

### Removed
- Dead donor code: `backends/`, `db/`, `DevRunner`, `runtime/events.py`,
  `runtime/tools.py`, `PluginRegistry.pick`, `skill_runtime` LLM/batch
  helpers; deps typer, rich, prompt-toolkit.

[Unreleased]: https://github.com/sagi5060/agentdeck/compare/v2.0.0b3...HEAD
[2.0.0b3]: https://github.com/sagi5060/agentdeck/compare/v2.0.0b2...v2.0.0b3
[2.0.0b2]: https://github.com/sagi5060/agentdeck/compare/v2.0.0b1...v2.0.0b2
[2.0.0b1]: https://github.com/sagi5060/agentdeck/compare/v1.2.1...v2.0.0b1
[1.2.1]: https://github.com/sagi5060/agentdeck/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/sagi5060/agentdeck/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/sagi5060/agentdeck/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/sagi5060/agentdeck/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/sagi5060/agentdeck/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sagi5060/agentdeck/releases/tag/v0.1.0
