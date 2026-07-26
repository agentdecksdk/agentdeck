# Changelog

All notable changes to agentdeck. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Add entries under **Unreleased** as you go;
they move under a version heading when a release is tagged.

## [Unreleased]

### Changed
- **Breaking:** `.agentdeck/` project layout now uses top-level type
  subdirectories — `agents/<bundle>/agent.py` and `workflows/<bundle>/workflow.py`
  instead of `<bundle>/agent.py` / `<bundle>/workflow.py` straight under the
  project root. `skills/*/SKILL.md` is unchanged. `PluginRegistry` gained a
  required `type_dir` field (`AgentRegistry`/`WorkflowRegistry` default it to
  `"agents"`/`"workflows"`). No migration shim — an old-layout project dir now
  raises a `ConfigError` pointing at the new paths instead of silently
  discovering nothing.

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

[Unreleased]: https://github.com/sagi5060/agentdeck/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/sagi5060/agentdeck/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/sagi5060/agentdeck/releases/tag/v0.1.0
