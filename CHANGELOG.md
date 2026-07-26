# Changelog

All notable changes to agentdeck. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/). Add entries under **Unreleased** as you go;
they move under a version heading when a release is tagged.

## [Unreleased]

### Added
- `App.open()` async context manager: runs `load()`, starts the MCP lifecycle,
  and guarantees `aclose()` on exit (even on error). `App.aclose()` closes the
  Redis session client and MCP servers; idempotent, safe to call twice.
- `App(session_factory=...)` DI seam: inject a prebuilt `SessionFactory` (e.g.
  wrapping fakeredis) instead of building one from settings — for tests.
- `agentdeck-serve` now wires `App.open()`/`aclose()` through a FastAPI
  lifespan, so `compose stop` (SIGTERM) shuts down the Redis client and MCP
  servers cleanly instead of leaking them.

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
