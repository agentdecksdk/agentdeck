# Changelog

All notable changes to agentdeck. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Add entries under **Unreleased** as you go;
they move under a version heading when a release is tagged.

## [Unreleased]

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

### Changed
- The streamed `done` event's `"output"` is now the SDK's `final_output`
  (matching non-streamed `chat()`, and the validated model for an
  `output_type` agent) instead of the re-joined text deltas, which disagreed
  for tool-using and structured-output agents.

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

[Unreleased]: https://github.com/sagi5060/agentdeck/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sagi5060/agentdeck/releases/tag/v0.1.0
