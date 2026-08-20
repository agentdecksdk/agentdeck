# AgentDeck Comprehensive Source Code Audit Report

**Date:** 2026-08-20
**Scope:** All 102 Python source files across `agentdeck/` (excluding tests and documentation).
**Verification Baseline:** `make check` passing (1753 tests, ruff, ty, import-linter all clean).

---

## Executive Summary

| Category | File Count | Description |
| :--- | :---: | :--- |
| **[KEEP AS IS]** | **78** | Meets all architectural invariants, typing standards, and error-handling requirements. |
| **[CHANGE / FIX]** | **17** | Specific fixes needed (e.g. `Deck(context=Any)` edge case in injection, unhandled `None` in `AgentNode`, scheme normalization in `compile.py`, style guide compliance regarding em dashes). |
| **[IMPROVE / EXTEND]** | **6** | Opportunities for enhancement (e.g. graceful HTTP degradation in `web_search.py`, multi-loop lock caching in `MCPLifecycle`, eager stream unwrap for 404s in `/v2/invocables/{name}/chat`). |
| **[DELETE]** | **1** | Dead / unreferenced code (`agentdeck/runtime/capture.py`). |
| **Total** | **102** | **100% of codebase scanned.** |

---

## Detailed File-by-File Audit

### 1. Root Entrypoints (9 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/__init__.py) | `[KEEP AS IS]` | Package entry point. Exports public API (`Deck`, `Agent`, `Workflow`, `Context`, exceptions). Correctly handles distribution naming difference (`agentdeck-sdk` vs `agentdeck`). |
| [`agentdeck/deck.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/deck.py) | `[KEEP AS IS]` | Primary composition root (1414 lines). Strict lifecycle enforcement (`NEW` -> `BUILT` -> `OPEN` -> `CLOSED`), immutable catalog, non-blocking run stream management, clean unwrap of agent / workflow results. |
| [`agentdeck/cli.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/cli.py) | `[KEEP AS IS]` | CLI control-plane runner (`agentdeck runs signal`). Wires SQLite `ControlPort` directly without importing surfaces. Idempotent signal dispatch. |
| [`agentdeck/composition.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/composition.py) | `[KEEP AS IS]` | Assembly root for `Runtime`. Safely resolves backends (`memory://`, `sqlite://`, `redis://`, `postgres://`) and keeps heavy optional dependencies (`psycopg`, `redis`) lazily imported. |
| [`agentdeck/errors.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/errors.py) | `[KEEP AS IS]` | Complete exception hierarchy rooted at `AgentdeckError`. Actionable error messages with resolution guidance. |
| [`agentdeck/mcp.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/mcp.py) | `[KEEP AS IS]` | `.mcp.json` reader and validator (`McpServerSettings`). Fails fast on invalid JSON or missing `mcpServers` object. |
| [`agentdeck/observers.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/observers.py) | `[KEEP AS IS]` | Composition-level telemetry tap (`Langfuse`). Correctly coordinates SDK OpenInference instrumentation order and enforces explicit configuration. |
| [`agentdeck/serve.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/serve.py) | `[KEEP AS IS]` | Minimal FastAPI server. Pulls opening events eagerly (`_opened`) to map `SessionBusyError` to 409 and `NotFoundError` to 404 before committing SSE headers. |
| [`agentdeck/testing.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/testing.py) | `[KEEP AS IS]` | Test harness (`ScriptedModel`, `patch_model`, `scripted_model_server`). Provides deterministic delta streaming, token accounting, and tool call sequencing. |

---

### 2. Core Domain & Schema (16 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/core/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/__init__.py) | `[KEEP AS IS]` | Innermost ring barrel export. Imports stdlib + pydantic only (enforced by `import-linter`). |
| [`agentdeck/core/base.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/base.py) | `[KEEP AS IS]` | `CoreModel` (`extra="ignore"`, `frozen=True`) and `JsonData` with non-recursive iterative validation rejecting `NaN`/`Infinity`. |
| [`agentdeck/core/content.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/content.py) | `[KEEP AS IS]` | Multimodal content blocks (`TextBlock`, `ImageBlock`, `AudioBlock`, `ResourceBlock`, `DataBlock`). Enforces 1 MB decoded inline bytes cap and lossless `UnknownBlock` round-tripping. |
| [`agentdeck/core/context.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/context.py) | `[KEEP AS IS]` | Immutable `RunContext` (slots=True) and public `Context[T]` view. Strict separation between application identity and run machinery. |
| [`agentdeck/core/control.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/control.py) | `[KEEP AS IS]` | Cooperative control signals (`CANCEL`, `PAUSE`, `RESUME`) and `Gate`. Polling interval bounded to 200ms with monotonic clock injection. |
| [`agentdeck/core/events.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/events.py) | `[KEEP AS IS]` | Envelope v3.1 schema. Major/minor version negotiation, discriminator-driven payload parsing, and graceful degradation to `UnknownEvent`. |
| [`agentdeck/core/invocable.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/invocable.py) | `[KEEP AS IS]` | `InvocableSpec` and `InvocableKind` (`agent`, `workflow`, `skill`). Strict `extra="forbid"`. |
| [`agentdeck/core/reporting.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/reporting.py) | `[KEEP AS IS]` | Out-of-band progress/status reporter with bounded memory queue (`MAX_PENDING_REPORTS = 64`). |
| [`agentdeck/core/status.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/status.py) | `[KEEP AS IS]` | Pure derivation of run lifecycle status from event log folds. Complete policy matrix mapping `(RunStatus, Operation)` and `(RunStatus, Signal)` with zero ambiguous paths. |
| [`agentdeck/core/ports/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/__init__.py) | `[KEEP AS IS]` | Re-exports all port interfaces. |
| [`agentdeck/core/ports/control.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/control.py) | `[KEEP AS IS]` | Abstract `ControlPort` (`signal`, `poll`, `consume`). |
| [`agentdeck/core/ports/engine.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/engine.py) | `[KEEP AS IS]` | Abstract `EnginePort` (`start`, `resume`). Generator-based payload streaming. |
| [`agentdeck/core/ports/lease.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/lease.py) | `[KEEP AS IS]` | Abstract `LeasePort` (`acquire`, `renew`, `release`, `dead`). Never infers death from absence of evidence. |
| [`agentdeck/core/ports/sink.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/sink.py) | `[KEEP AS IS]` | Abstract `EventSinkPort` (`start`, `emit`, `close`). |
| [`agentdeck/core/ports/store.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/store.py) | `[KEEP AS IS]` | Abstract `EventStorePort` (`append`, `read`, `read_run`, `claim_start`, `claim_resume`, `list_runs`, `find_by_key`, `run_status`). |
| [`agentdeck/core/ports/tools.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/core/ports/tools.py) | `[KEEP AS IS]` | `ToolSet` and synchronous `ToolSourcePort.resolve`. |

---

### 3. Runtime Machinery (7 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/runtime/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/__init__.py) | `[IMPROVE / EXTEND]` | Docstring references legacy "workspace" concept; update to reflect current runtime scope. |
| [`agentdeck/runtime/capture.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/capture.py) | `[DELETE]` | `CaptureActor` and `Capture` models are unused (0 callers across codebase). Delete dead code. |
| [`agentdeck/runtime/discovery.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/discovery.py) | `[KEEP AS IS]` | `InvocableRegistry` discovering agents and workflows. Detects naming collisions and bridges context nodes eagerly. |
| [`agentdeck/runtime/dispatch.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/dispatch.py) | `[KEEP AS IS]` | `SinkDispatch` event fan-out with circuit breaker, timeout limits (5s), bounded buffers (256), and cancellation-safe flushing. |
| [`agentdeck/runtime/registry.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/registry.py) | `[KEEP AS IS]` | `PluginRegistry[T]` for `.agentdeck/` bundle loading with synthetic module mounting and cache eviction. |
| [`agentdeck/runtime/service.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/service.py) | `[KEEP AS IS]` | Core `Runtime` service. Implements store-assigned sequence stamping, atomic claims, non-blocking control routing, and report buffer draining. |
| [`agentdeck/runtime/settings.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/settings.py) | `[KEEP AS IS]` | Layered Pydantic settings (`AGENTDECK_*` and `config.yaml`). Rejects retired v2 environment variables. |

---

### 4. Authoring Layer & Declarations (18 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/authoring/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/__init__.py) | `[CHANGE / FIX]` | Replace prohibited ` - ` in docstring (line 3). |
| [`agentdeck/authoring/agent.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/agent.py) | `[CHANGE / FIX]` | Add explicit return type `SDKAgent` under `TYPE_CHECKING` for `build()`; replace `ValueError` with `ConfigError` at line 126; clean ` - ` docstring characters. |
| [`agentdeck/authoring/compile.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/compile.py) | `[CHANGE / FIX]` | Normalize postgres schemes (`scheme in ("postgresql", "postgres")`); add prefix check before slicing stale instructions in `refresh_mcp_status` (lines 212-219); clean ` - ` in docstrings. |
| [`agentdeck/authoring/graphs.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/graphs.py) | `[IMPROVE / EXTEND]` | In `inject_context`, check `inspect.iscoroutinefunction(getattr(target, "__call__", None))` to support custom async callable class instances; clean ` - ` in comments. |
| [`agentdeck/authoring/hooks.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/hooks.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings and comments. |
| [`agentdeck/authoring/injection.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/injection.py) | `[CHANGE / FIX]` | Fix `declared_context_type` to allow `value is Any` (line 159), which currently fails `isinstance(Any, type)` and breaks `Deck(context=Any)`; clean ` - ` in docstrings. |
| [`agentdeck/authoring/instructions.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/instructions.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings and comments. |
| [`agentdeck/authoring/interrupts.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/interrupts.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings. |
| [`agentdeck/authoring/nodes.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/nodes.py) | `[CHANGE / FIX]` | In `LoadFileNode` (line 67), replace bare `RuntimeError` with `ConfigError`; in `AgentNode.__call__` (line 124), guard against `done is None` when streaming terminates early without `StreamDone`; clean ` - `. |
| [`agentdeck/authoring/runners/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/runners/__init__.py) | `[KEEP AS IS]` | Clean barrel export of runner classes. |
| [`agentdeck/authoring/runners/agent.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/runners/agent.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings and comments. |
| [`agentdeck/authoring/runners/workflow.py`](file:///home/sagi5060/prjs/agentdeck/authoring/runners/workflow.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings. |
| [`agentdeck/authoring/skills.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/skills.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings. |
| [`agentdeck/authoring/state.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/state.py) | `[CHANGE / FIX]` | In `coerce_input` (line 26), use `getattr(schema, '__name__', str(schema))` to avoid `AttributeError` on non-class type annotations; clean ` - `. |
| [`agentdeck/authoring/timers.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/timers.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings. |
| [`agentdeck/authoring/tools.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/tools.py) | `[CHANGE / FIX]` | Clean ` - ` in docstrings and comments. |
| [`agentdeck/authoring/web_search.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/web_search.py) | `[IMPROVE / EXTEND]` | Wrap Tavily HTTP calls in `try/except httpx.HTTPError` to return structured tool error strings instead of crashing turn execution on transient network/API issues. |
| [`agentdeck/authoring/workflow.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/workflow.py) | `[CHANGE / FIX]` | In `as_tool` (line 232), handle both dict and Pydantic model outputs when filtering `output_keys`; standardize `ValueError` to `ConfigError` at lines 85, 90; clean ` - `. |

---

### 5. Skills System (2 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/skills/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/skills/__init__.py) | `[CHANGE / FIX]` | Skill directory scanner and instruction generator. Clean ` - ` in error messages and docstrings. |
| [`agentdeck/skills/bundle.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/skills/bundle.py) | `[KEEP AS IS]` | `SkillBundle` YAML frontmatter and markdown parser. Strict error translation to `ConfigError`. |

---

### 6. Surfaces & Ingress (7 files)

| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/surfaces/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/__init__.py) | `[KEEP AS IS]` | Ring 3 package boundary documentation. |
| [`agentdeck/surfaces/cli/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/cli/__init__.py) | `[KEEP AS IS]` | CLI re-exports. |
| [`agentdeck/surfaces/cli/chat.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/cli/chat.py) | `[KEEP AS IS]` | Terminal SSE stream renderer. Clean pattern matching across event kinds. |
| [`agentdeck/surfaces/serve/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/__init__.py) | `[KEEP AS IS]` | Re-exports `build_app`. |
| [`agentdeck/surfaces/serve/app.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/app.py) | `[IMPROVE / EXTEND]` | In `POST /v2/invocables/{name}/chat`, catch `NotFoundError` on initial pull and map to HTTP 404 rather than unhandled 500. |
| [`agentdeck/surfaces/serve/compat.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/compat.py) | `[KEEP AS IS]` | Legacy wire-format rendering (`delta`, `done`, `node_update`, `interrupt`). Robust aggregation and error handling. |
| [`agentdeck/surfaces/serve/workflows.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/workflows.py) | `[IMPROVE / EXTEND]` | In `POST /v2/resume`, pull the opening event eagerly before returning `StreamingResponse` to map `RunStateError` to 409 prior to sending headers. |

---

### 7. Adapters (43 files)

#### Control Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/__init__.py) | `[KEEP AS IS]` | Ring 2 adapter isolation boundary. |
| [`agentdeck/adapters/control/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/control/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/control/memory/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/control/memory/__init__.py) | `[KEEP AS IS]` | Re-exports `MemoryControlPort`. |
| [`agentdeck/adapters/control/memory/port.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/control/memory/port.py) | `[KEEP AS IS]` | Dict-backed in-memory `ControlPort`. Atomic compare-and-swap semantics. |
| [`agentdeck/adapters/control/sqlite/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/control/sqlite/__init__.py) | `[KEEP AS IS]` | Re-exports `SqliteControlPort`. |
| [`agentdeck/adapters/control/sqlite/port.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/control/sqlite/port.py) | `[KEEP AS IS]` | SQLite cross-process `ControlPort`. Thread-safe WAL mode, automated migrations, maps driver exceptions to `StoreError`. |

#### Lease Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/leases/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/leases/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/leases/memory/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/leases/memory/__init__.py) | `[KEEP AS IS]` | Re-exports `MemoryLeasePort`. |
| [`agentdeck/adapters/leases/memory/port.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/leases/memory/port.py) | `[KEEP AS IS]` | In-memory `LeasePort` with injectable clock. Conservative dead-run reporting. |
| [`agentdeck/adapters/leases/sqlite/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/leases/sqlite/__init__.py) | `[KEEP AS IS]` | Re-exports `SqliteLeasePort`. |
| [`agentdeck/adapters/leases/sqlite/port.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/leases/sqlite/port.py) | `[KEEP AS IS]` | SQLite multi-worker `LeasePort`. Clock skew resilient (evaluates against backend SQLite time). |

#### Store Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/stores/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/stores/memory/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/memory/__init__.py) | `[KEEP AS IS]` | Re-exports `MemoryEventStore`. |
| [`agentdeck/adapters/stores/memory/store.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/memory/store.py) | `[KEEP AS IS]` | In-memory reference `EventStorePort`. Atomic `claim_start` / `claim_resume` sequence stamping. |
| [`agentdeck/adapters/stores/postgres/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/postgres/__init__.py) | `[KEEP AS IS]` | Re-exports `PostgresEventStore`. |
| [`agentdeck/adapters/stores/postgres/store.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/postgres/store.py) | `[KEEP AS IS]` | Postgres `EventStorePort`. Per-log advisory locks, deterministic 64-bit blake2b hash, lazy DDL migrations, clean `DuplicateKeyError` mapping. |
| [`agentdeck/adapters/stores/redis/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/redis/__init__.py) | `[KEEP AS IS]` | Re-exports `RedisEventStore`. |
| [`agentdeck/adapters/stores/redis/store.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/redis/store.py) | `[KEEP AS IS]` | Redis `EventStorePort`. Optimistic concurrency via `WATCH`/`MULTI`/`EXEC`, bounded retry loop (64 attempts), URL segment escaping. |
| [`agentdeck/adapters/stores/sqlite/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/sqlite/__init__.py) | `[KEEP AS IS]` | Re-exports `SqliteEventStore`. |
| [`agentdeck/adapters/stores/sqlite/store.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/stores/sqlite/store.py) | `[KEEP AS IS]` | SQLite `EventStorePort`. `BEGIN IMMEDIATE` transaction locking, millisecond UTC timestamps, group-by status summarization. |

#### Engine Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/engines/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/engines/langgraph/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/langgraph/__init__.py) | `[KEEP AS IS]` | Re-exports LangGraph engine components. |
| [`agentdeck/adapters/engines/langgraph/checkpointer.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/langgraph/checkpointer.py) | `[KEEP AS IS]` | Checkpointer factory caching instances per `AbstractEventLoop` using `WeakKeyDictionary`. Daemon thread on aiosqlite worker prevents shutdown hangs. |
| [`agentdeck/adapters/engines/langgraph/engine.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/langgraph/engine.py) | `[KEEP AS IS]` | LangGraph adapter over `StateGraph.astream`. Manages node updates, interrupts, safe-point checks, stream writers, and JSON serialization. |
| [`agentdeck/adapters/engines/openai_agents/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/__init__.py) | `[KEEP AS IS]` | Re-exports OpenAI Agents engine components. |
| [`agentdeck/adapters/engines/openai_agents/engine.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/engine.py) | `[KEEP AS IS]` | OpenAI Agents SDK engine adapter. Multimodal block streaming, turn reconciliation, detached task cancellation via `Launch`. |
| [`agentdeck/adapters/engines/openai_agents/reconcile.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/reconcile.py) | `[KEEP AS IS]` | Session history reconciliation against event log post-crash. Emits divergence custom events when non-tail divergence occurs. |
| [`agentdeck/adapters/engines/openai_agents/runconfig.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/runconfig.py) | `[KEEP AS IS]` | Translates `RunSettings` into SDK `RunConfig`. Custom CA bundle configuration and handoff history formatting. |
| [`agentdeck/adapters/engines/openai_agents/sessions.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/sessions.py) | `[KEEP AS IS]` | Session store abstraction supporting Redis and fallback SQLite sessions with clean teardown. |
| [`agentdeck/adapters/engines/openai_agents/translate.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/openai_agents/translate.py) | `[IMPROVE / EXTEND]` | Stream translator. Extend `_tool_call_started` when non-function tool calls (e.g. computer use) enter scope (TODO #52). |
| [`agentdeck/adapters/engines/stub/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/stub/__init__.py) | `[KEEP AS IS]` | Re-exports `StubEngine`. |
| [`agentdeck/adapters/engines/stub/engine.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/engines/stub/engine.py) | `[KEEP AS IS]` | Reference engine for deterministic test execution. |

#### Telemetry Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/telemetry/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/telemetry/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/telemetry/langfuse/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/telemetry/langfuse/__init__.py) | `[KEEP AS IS]` | Re-exports `LangfuseSink`. |
| [`agentdeck/adapters/telemetry/langfuse/client.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/telemetry/langfuse/client.py) | `[KEEP AS IS]` | Isolated Langfuse SDK boundary with OTel timeout configuration and deterministic trace key seeds. |
| [`agentdeck/adapters/telemetry/langfuse/sink.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/telemetry/langfuse/sink.py) | `[KEEP AS IS]` | Langfuse `EventSinkPort`. Redacts inline base64 URIs, enforces bounded open trace memory caps, and flushes asynchronously. |
| [`agentdeck/adapters/telemetry/langfuse/trace.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/telemetry/langfuse/trace.py) | `[KEEP AS IS]` | Structural protocols for tracer decoupling. |

#### Tool Adapters
| File | Status | Evaluation & Action Items |
| :--- | :---: | :--- |
| [`agentdeck/adapters/tools/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/__init__.py) | `[KEEP AS IS]` | Package namespace. |
| [`agentdeck/adapters/tools/mcp/__init__.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/mcp/__init__.py) | `[KEEP AS IS]` | Re-exports MCP components. |
| [`agentdeck/adapters/tools/mcp/lifecycle.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/mcp/lifecycle.py) | `[IMPROVE / EXTEND]` | Cache `_lock` per event loop (like checkpointer) to avoid cross-loop lock reuse issues in multi-loop test harnesses. |
| [`agentdeck/adapters/tools/mcp/source.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/mcp/source.py) | `[KEEP AS IS]` | `ToolSourcePort` querying MCP lifecycle and building degraded prompt banners. |
| [`agentdeck/adapters/tools/mcp/transport.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/mcp/transport.py) | `[KEEP AS IS]` | Resilient HTTP transport with automatic reconnection and session loss replay. |
| [`agentdeck/adapters/tools/mcp/wiring.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/adapters/tools/mcp/wiring.py) | `[KEEP AS IS]` | MCP prompt degradation banners and tool set filtering. |

---

## Action Plan & Recommendations

1. **Delete Dead Code:**
   - Remove [`agentdeck/runtime/capture.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/runtime/capture.py).
2. **Apply Bug Fixes & Edge-Case Hardening:**
   - [`agentdeck/authoring/injection.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/injection.py): Support `value is Any` in `declared_context_type`.
   - [`agentdeck/authoring/nodes.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/nodes.py): Guard against `done is None` in `AgentNode.__call__`.
   - [`agentdeck/authoring/compile.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/compile.py): Add safety guard before slicing in `refresh_mcp_status`.
   - [`agentdeck/authoring/workflow.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/workflow.py): Support Pydantic model outputs in `as_tool`.
   - [`agentdeck/authoring/state.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/state.py): Use `getattr(schema, '__name__', str(schema))` in `coerce_input`.
3. **Enhance Resilience & Lifecycle:**
   - [`agentdeck/authoring/web_search.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/authoring/web_search.py): Handle `httpx.HTTPError` gracefully.
   - [`agentdeck/adapters/tools/mcp/lifecycle.py`](file:///home/sagi5060/prjs/agentdeck/adapters/tools/mcp/lifecycle.py): Cache lock per active event loop.
   - [`agentdeck/surfaces/serve/app.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/app.py): Map `NotFoundError` to HTTP 404.
   - [`agentdeck/surfaces/serve/workflows.py`](file:///home/sagi5060/prjs/agentdeck/agentdeck/surfaces/serve/workflows.py): Pull first event eagerly on resume.
4. **Enforce House Style Invariants:**
   - Strip all prohibited ` - ` em dash sequences from docstrings, comments, and exception messages across authoring and skills.
