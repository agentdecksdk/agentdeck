# Changelog

All notable changes to agentdeck. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Add entries under **Unreleased** as you go;
they move under a version heading when a release is tagged.

## [Unreleased]

### Added
- Golden wire baselines (`tests/golden/`): byte-level snapshots of the HTTP/SSE
  surface against a scripted fake model, replayed by `make test`. Re-record
  deliberately with `make golden`; see `tests/golden/README.md`.
- `import-linter` contracts (`.importlinter`) run by `make lint-imports`, and both
  are wired into `make check` / CI.
- `agentdeck.core`: the canonical event schema v1 (`events.py`) and content blocks
  (`content.py`) — a closed eight-field `Event` envelope over a payload union
  discriminated by `kind`, `parse_event()` tolerating unknown kinds and fields, and
  the `check_contiguous` / `check_terminal` ordering invariants. Nothing imports it
  yet; runtime behaviour is unchanged. One serialization per kind is frozen under
  `tests/core/snapshots/`, and an import-linter contract keeps core on stdlib +
  pydantic only.

## [1.2.1] - 2026-08-03

No changes to the `agentdeck` package itself — this version covers the
documentation platform and its CI.

### Added
- `docs-site/`: MDX documentation platform built on Nextra 4 and the Next.js
  App Router, statically exported to GitHub Pages under `/agentdeck`.
  `docs-pages.yml` builds and deploys it when a GitHub Release is published;
  `docs-check.yml` builds it on every PR that touches `docs-site/`.

### Fixed
- Docs build failed to prerender every page (`expected nonoptional, received
  undefined` at `children`). `nextra-theme-docs@4.6.1`'s `<Layout>` strips
  `children` off its props before validating them against a schema that still
  requires `children`; zod 4.4.0 turned that into a hard error. zod is pinned
  to `4.3.5` via `overrides`, `docs-site/package-lock.json` is committed, and
  both docs workflows install with `npm ci` so resolution stops drifting.
- Docs "Edit this page" links pointed at a feature branch and now point at
  `dev`.

## [1.2.0] - 2026-07-28

### Added
- `AGENTDECK_LANGFUSE_BASE_URL`: Langfuse 4.x endpoint name; wins over
  `AGENTDECK_LANGFUSE_HOST` (kept as the legacy alias) and is mirrored to
  sandboxed skills as both `LANGFUSE_BASE_URL` and `LANGFUSE_HOST`.

## [1.1.0] - 2026-07-27

### Added
- `BaseAgent.handoffs` entries may now be a `str` registry name, resolved lazily
  at `build()` time via the same discovery registry `App` uses — two agents that
  hand off to each other no longer need to import each other's module. Unknown
  names raise `NotFoundError` naming the available agents; mutual handoffs
  resolve without recursing forever.
- Durable timer waits (#22): `agentdeck.workflows.sleep_until(when)` pauses a node in a
  `durable = True` workflow until a timezone-aware wall-clock moment, built on `interrupt()`
  — a payload convention (`{"type": "timer", "wake_at": ...}`) so a timer-paused thread is
  distinguishable from a human-paused one in the inbox. `App.due_resumes(now=None)` filters
  `pending_interrupts()` to timer threads whose wake time has passed; `App.tick(now=None)`
  resumes every due thread (resume value = its wake timestamp). Callers own the scheduling
  cadence (cron, systemd timer, a loop) — agentdeck runs no daemon. Naive datetimes are
  rejected with a clear `ValueError`.

### Fixed
- `HeadlessRunner.run`/`run_streamed` now forward the chat `session_id` (read
  off the SDK `session` object) into `trace_run`, so an `App.chat(...,
  session_id=...)` turn's root trace carries that session id in Langfuse
  instead of always tracing with a null session — per-customer trace grouping
  was silently broken. `trace_run` gains an optional `session_id` keyword that
  wins over the capture-derived identity at a run root; nested units are
  unaffected.

## [1.0.0] - 2026-07-27

### Fixed
- `resolve_checkpointer`'s sqlite backend no longer raises `RuntimeError: no
  running event loop` when `App.load()` (or any other sync caller) builds a
  `durable=True` workflow outside a running loop — `AsyncSqliteSaver`'s
  constructor calls `asyncio.get_running_loop()` itself, so it's now built
  inside the same `_run_sync` call as the connect, matching the postgres path.
### Added
- Human-in-the-loop for `durable = True` workflows: a node calling
  `langgraph.types.interrupt(payload)` (re-exported as
  `agentdeck.workflows.interrupt`) pauses the run, and `run_workflow` returns
  `{"type": "interrupt", "payload": ..., "thread_id": ...}` instead of a final
  state. `App.resume_workflow(name, thread_id, value)` answers it (returning the
  final state or the next interrupt) and `App.pending_interrupts(name=None)`
  lists every thread still waiting — the approval inbox. Same trio on
  `BaseWorkflow` as `run` / `resume` / `pending`.
- `run_workflow_stream` ends a paused run with that same interrupt event in place
  of its terminal `done` event, and the SSE endpoint emits it as an `interrupt`
  event instead of `done`.
- `GET /workflows/{name}/pending` and `POST /workflows/{name}/{thread_id}/resume`
  (`{"value": ...}`); `POST /workflows/{name}` takes an optional `thread_id`
  query parameter so durable runs can be started over HTTP.

- `App.run_workflow_stream(name, state=None, thread_id=None)`: async iterator over a
  workflow's `astream(stream_mode=["updates", "custom"])` — a `node_update` event per
  completed node, a `custom` event per `langgraph.config.get_stream_writer()` call, then one
  terminal `done` event carrying the final state. Same `thread_id` semantics as
  `run_workflow`, which is unchanged.
- `AgentNode` now forwards its nested agent's text deltas into the graph's custom stream via
  `get_stream_writer()` (a no-op outside `run_workflow_stream`), so a workflow-driven chat
  streams tokens the same as a direct agent chat.
- `POST /workflows/{name}?stream=true`: `text/event-stream` response mirroring the chat
  endpoint's pattern — `node_update`/`custom` `message` events, a terminal `done` event with
  the final state, or an `error` event on a mid-stream failure.
- `subagents = [...]` class attribute on `BaseAgent`: opt-in `spawn_subagent`
  `FunctionTool` that lets the model delegate a task to another registered
  agent at runtime. The subagent runs as an isolated `HeadlessRunner`
  one-shot (no session, no shared history — the task text is its entire
  context) and its `final_output` is returned as a string. Spawning a name
  outside the allowlist, or attempting to spawn from inside an already-
  spawned subagent (depth-limited via a `ContextVar`, default depth 1),
  returns an `error: ...` string instead of raising, so the run continues.
  New module `agentdeck/agents/subagents.py`.

### Changed
- **Breaking:** `.agentdeck/` project layout now uses top-level type
  subdirectories — `agents/<bundle>/agent.py` and `workflows/<bundle>/workflow.py`
  instead of `<bundle>/agent.py` / `<bundle>/workflow.py` straight under the
  project root. `skills/*/SKILL.md` is unchanged. `PluginRegistry` gained a
  required `type_dir` field (`AgentRegistry`/`WorkflowRegistry` default it to
  `"agents"`/`"workflows"`). No migration shim — an old-layout project dir now
  raises a `ConfigError` pointing at the new paths instead of silently
  discovering nothing.
- A non-durable workflow whose node calls `interrupt()` now raises `ConfigError`
  instead of silently returning an unresumable state.

## [0.2.0] - 2026-07-26

### Added
- `App.chat_stream(name, session_id, message)`: async iterator of text deltas
  followed by a terminal `StreamDone(final_output, usage)`, wrapping the Agents
  SDK `Runner.run_streamed` with the same session semantics as `chat()`.
  `HeadlessRunner.run_streamed` is the runner-layer counterpart to `run`,
  honoring `run_config` / `max_turns` / sandbox attachment / trace_run
  identically; it cancels the SDK run loop when the generator is closed or
  abandoned, and records failed turns on the trace.
- `POST /agents/{name}/chat?stream=true`: `text/event-stream` response with
  incremental `delta` events and a final `done` event carrying
  `{"output", "usage"}`. A failure mid-stream emits an `error` event; a
  request missing `session_id` / `message` is rejected with 422 before the
  stream starts. Sent with `Cache-Control: no-cache` and
  `X-Accel-Buffering: no` so proxies don't buffer the stream.

- `agentdeck/errors.py`: one exception hierarchy — `AgentdeckError` base,
  `NotFoundError` (unknown agent/workflow/skill), `SkillError` (base for
  `SkillExecutionError`, `SkillEnvError`), `ConfigError`. Exported from
  `agentdeck` alongside `App`.
- `App.open()` async context manager: runs `load()`, starts the MCP lifecycle,
  and guarantees `aclose()` on exit (even on error). `App.aclose()` closes the
  Redis session client and MCP servers; idempotent, safe to call twice.
- `App(session_factory=...)` DI seam: inject a prebuilt `SessionFactory` (e.g.
  wrapping fakeredis) instead of building one from settings — for tests.
- `agentdeck-serve` now wires `App.open()`/`aclose()` through a FastAPI
  lifespan, so `compose stop` (SIGTERM) shuts down the Redis client and MCP
  servers cleanly instead of leaking them.
- `App.load()` stashes its result on `App.inventory`, so `/health` no longer
  re-runs the whole compile pass on boot.

- Workflow durability: `BaseWorkflow.durable: ClassVar[bool] = False` opt-in.
  `durable=True` compiles the graph with a LangGraph checkpointer resolved from
  a new `CheckpointSettings` group (`AGENTDECK_CHECKPOINT_*`, YAML `checkpoint:`
  — `backend`: `sqlite` | `postgres` | `memory`, `url`). `App.run_workflow` and
  `BaseWorkflow.run` accept `thread_id: str | None = None`, threaded into
  LangGraph's `config={"configurable": {"thread_id": ...}}` so a run can
  resume; `durable=True` with no `thread_id` raises. `durable=False` (default)
  compiles and runs exactly as before. New optional `[durability]` extra
  (`langgraph-checkpoint-sqlite`, `langgraph-checkpoint-postgres`) — a missing
  extra raises a clear `ImportError` at first use instead of a bare
  `ModuleNotFoundError`. `memory`/`sqlite` are exercised in tests
  (`tests/test_workflow_durability.py`), including a real cross-process restart
  against a sqlite file, matching the issue's acceptance test.

### Changed
- The streamed `done` event's `"output"` is now the SDK's `final_output`
  (matching non-streamed `chat()`, and the validated model for an
  `output_type` agent) instead of the re-joined text deltas, which disagreed
  for tool-using and structured-output agents.
- `agentdeck-serve` answers `503` on every endpoint before the lifespan has
  started the `App` (`/health` reports `{"status": "starting"}`) instead of
  raising `AttributeError` or reporting an empty inventory as `ok`.
- `App.aclose()` tears down the process-wide MCP lifecycle only if that `App`
  started it, and always runs both cleanup steps even if one fails.
- `PluginRegistry.get` / `SkillRegistry.get` now raise `NotFoundError`
  instead of bare `KeyError`.
- Invalid configuration now raises `ConfigError` instead of `ValueError`:
  unusable `compaction.threshold`/`model` combinations, `skills_dir` and
  skill allow-list problems, malformed MCP server entries, and malformed
  `SKILL.md` frontmatter. Pydantic field validators still raise `ValueError`
  (pydantic requires it).
- `SkillResult.raise_if_failed` raises `SkillExecutionError` and
  `SkillResult.require_output` raises `SkillError`, both instead of bare
  `RuntimeError`. `SkillExecutionError` moved to `agentdeck.skills.executor`
  and is re-exported from `agentdeck.skills` / `agentdeck.workflows`.
- `agentdeck-serve` maps `NotFoundError` to HTTP 404 with the message as the
  body; every other `AgentdeckError` is a server fault and now returns 500
  with a fixed `{"detail": "internal error"}` body, the real detail logged
  server-side. Previously these returned 422 with the exception message,
  which could echo skill stderr back to the client.

## [0.1.0] - 2026-07-26

### Added
- `App` single entry point: discovers and builds agents / workflows / skills
  from the `./.agentdeck` project dir; `run_agent`, `run_workflow`, `chat`
  (session memory via Redis or in-process SQLite fallback), `session_for`.
- `agentdeck-serve` FastAPI surface: `/health`, `/agents/{name}/chat`,
  `/workflows/{name}` (`[serve]` extra).
- `web_search` function tool (Tavily-backed, model-agnostic).
- `runtime/capture.py`: `Capture` / `CaptureActor` / `CAPTURE_ENV` wire
  contract (reconstruction of the never-extracted `sysagents_core`).
- Packaging: pyproject with `serve` / `dev` / `observability` extras,
  Makefile (`make check` = lint + typecheck + tests), Dockerfile + compose
  (app + Redis), `.env.example`, pre-commit, CI + tag-driven release workflow.

### Changed
- Extracted from SysAgentsHarness and renamed: package `sysagent` →
  `agentdeck`, env prefixes `SYSAGENT_*` → `AGENTDECK_*`.
- Neutralized donor defaults (private GAIA endpoint, model, MCP hosts);
  empty `OPENAI_BASE_URL` now means the SDK default.
- Pinned `openai==2.32.0` to match `openai-agents==0.17.0` (2.33+ added
  required usage fields that crash the run loop).
- `BaseAgent.run()` is a one-shot headless run (the interactive REPL relied
  on donor code that was never extracted).

### Removed
- Dead donor code: `backends/`, `db/`, `DevRunner`, `runtime/events.py`,
  `runtime/tools.py`, `PluginRegistry.pick`, `skill_runtime` LLM/batch
  helpers; deps typer, rich, prompt-toolkit.

[Unreleased]: https://github.com/sagi5060/agentdeck/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/sagi5060/agentdeck/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/sagi5060/agentdeck/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sagi5060/agentdeck/releases/tag/v0.1.0
