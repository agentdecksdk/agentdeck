# Dependency Audit: agentdeck

Source: `pyproject.toml` (repo root), branch `audit/sdk-report`.

## 1. Declared dependencies

Package: `agentdeck-sdk` (name), imported as `agentdeck`. `requires-python = ">=3.12"`.

### Core (always installed)

| Dep | Pin | Style |
|---|---|---|
| pydantic | >=2.7 | range |
| pydantic-settings | >=2.4 | range |
| python-dotenv | >=1.0 | range |
| openai-agents | ==0.17.0 | exact |
| openai | ==2.32.0 | exact |
| httpx | >=0.27 | range |
| pyyaml | >=6.0 | range |
| langgraph | >=1.1.10 | range |
| langchain-core | >=0.3 | range |
| langgraph-checkpoint-sqlite | >=2.0 | range |

`openai-agents` and `openai` are pinned exact as a matched pair (comment: openai 2.33+ added required usage fields agents 0.17.0 doesn't set, pyproject.toml:27-28).

### Optional extras

| Extra | Deps | Pin style |
|---|---|---|
| serve | fastapi>=0.115, uvicorn>=0.30 | range |
| observability | langfuse>=2.60, openinference-instrumentation-openai-agents>=0.1, opentelemetry-sdk>=1.27 | range |
| durability | langgraph-checkpoint-postgres>=2.0, psycopg[binary]>=3.2 | range |
| redis | redis>=5.0 | range |

### Dev

| Dep | Pin | Style | Note |
|---|---|---|---|
| pytest | >=8.0 | range | |
| pytest-asyncio | >=0.23 | range | |
| pytest-cov | >=5.0 | range | not part of `make check` (pyproject.toml:70-72) |
| redis | >=5.0 | range | duplicated from `[redis]` extra, so gate covers redis paths without making it a base extra |
| ruff | >=0.6 | range | |
| ty | >=0.0.1a21 | range | |
| pre-commit | >=4.0 | range | |
| import-linter | ==2.13 | exact | pinned so a linter upgrade can't silently break the passing contract |

## 2. Declared vs. imported

| Dep | Import name | Status |
|---|---|---|
| pydantic | `pydantic` | used, widespread (mcp.py:16, engine.py:44/46, workflow.py:17, state.py:11, ...) |
| pydantic-settings | `pydantic_settings` | used, runtime/settings.py:29-30 |
| python-dotenv | `dotenv` | used, runtime/settings.py:27 |
| openai-agents | `agents` | used, testing.py:24-26, adapters/engines/openai_agents/runconfig.py:26, sessions.py:23 |
| openai | `openai` | used, testing.py:27/36, runconfig.py:27, authoring/runners/agent.py:23 |
| httpx | `httpx` | used, guarded in 2 spots (below), eager in 3 |
| pyyaml | `yaml` | used, skills/bundle.py:10 (only 1 import site) |
| langgraph | `langgraph` | used, adapters/engines/langgraph/engine.py:43-45, authoring/nodes.py:17, authoring/graphs.py:29 |
| langchain-core | `langchain_core` | used, authoring/runners/workflow.py:18 (only 1 import site) |
| langgraph-checkpoint-sqlite | `langgraph.checkpoint.sqlite` | used, adapters/engines/langgraph/checkpointer.py:153 |
| fastapi | `fastapi` | used, surfaces/serve/app.py:11-12, surfaces/serve/workflows.py:14-15 |
| uvicorn | `uvicorn` | used, serve.py:354 (guarded) |
| langfuse | `langfuse` | used, adapters/telemetry/langfuse/client.py:60,102 (guarded) |
| openinference-instrumentation-openai-agents | `openinference` | used, observers.py:37 (guarded) |
| opentelemetry-sdk | `opentelemetry` | **declared-but-never-imported** - only appears as a logger-name string literal (adapters/telemetry/langfuse/client.py:36: `_OTLP_EXPORTER_LOGGER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"`), no `import opentelemetry` anywhere |
| langgraph-checkpoint-postgres | `langgraph.checkpoint.postgres` | used, adapters/engines/langgraph/checkpointer.py:184 (guarded) |
| psycopg | `psycopg` | used, checkpointer.py:191 (guarded), adapters/stores/postgres/store.py:23-25 (eager, but module itself lazily imported - see 3) |
| redis | `redis` | used, adapters/stores/redis/store.py:36-37 (eager, but module itself lazily imported - see 3) |

**Imported but not declared:**

| Import | Where | Note |
|---|---|---|
| `aiosqlite` | adapters/engines/langgraph/checkpointer.py:152,160,163 | not stdlib, not in pyproject. Transitive dep of `langgraph-checkpoint-sqlite` (uv.lock:908-914 lists it as a direct dependency of that package). Imported and used directly (`aiosqlite.connect`, `conn._thread.daemon`), not just re-exported by langgraph - a version bump that drops it from langgraph-checkpoint-sqlite's deps would break this import silently. |

No other non-stdlib import root found without a matching declared dependency.

## 3. Guarded vs. module-top-level imports

| Package | Site | Guard |
|---|---|---|
| httpx | adapters/engines/openai_agents/runconfig.py:25, adapters/tools/mcp/transport.py:16, authoring/web_search.py:12, authoring/runners/agent.py:21 | top-level, eager (base dep, expected) |
| httpx | surfaces/cli/chat.py:34 | `TYPE_CHECKING` guard only (type-check import, no runtime cost) |
| yaml | skills/bundle.py:10 | top-level, eager (base dep) |
| fastapi | surfaces/serve/app.py:11-12, surfaces/serve/workflows.py:14-15 | top-level within `surfaces/serve/`, but that package is only reached via `Deck.asgi()`, which does `from agentdeck.serve import build_asgi_app` **inside the method body** (deck.py:1100-1109). So fastapi is not imported by a plain `import agentdeck` / `Deck.run()` path. |
| uvicorn | serve.py:354 | function-local import inside `main()`, only hit by the `agentdeck-serve` console script |
| langfuse | adapters/telemetry/langfuse/client.py:60,102 | function-local, inside `try`-guarded import comments (`ty: ignore[unresolved-import] - [observability] extra`) |
| openinference | observers.py:37 | function-local, same guard style |
| langgraph.checkpoint.postgres | adapters/engines/langgraph/checkpointer.py:184 | inside `try/except ImportError`, raises a clear message naming the `[durability]` extra if missing (checkpointer.py:184-190) |
| psycopg | checkpointer.py:191 | function-local, right after the postgres saver import |
| psycopg | adapters/stores/postgres/store.py:23-25 | module-top-level, but `agentdeck.adapters.stores.postgres` itself is only imported lazily from composition.py:249 (`from agentdeck.adapters.stores.postgres import PostgresEventStore`, inside a function) |
| redis | adapters/stores/redis/store.py:36-37 | module-top-level, but `agentdeck.adapters.stores.redis` is only imported lazily from composition.py:240 |
| aiosqlite | checkpointer.py:152 | function-local, inside the sqlite-checkpointer builder; comment (checkpointer.py:150) notes no guard needed since `langgraph-checkpoint-sqlite` is a base dep |

Pattern: every optional-extra package is either function-local at its own import site, or eager inside a submodule that is itself only reached through a lazy import one level up. No optional dep is imported at true module-load time of `agentdeck`'s always-imported paths (`__init__.py`, `deck.py`, `composition.py` top level).

## 4. Heavy transitive risk

| Dep | Status | Gate | Note |
|---|---|---|---|
| openai-agents | core | - | exact-pinned to 0.17.0, paired with openai==2.32.0 |
| langgraph | core | - | >=1.1.10, core dep, pulls `langgraph-checkpoint-sqlite` (base) which pulls `aiosqlite` + `sqlite-vec` (comment pyproject.toml:33-36: sqlite-vec is "unrelated to checkpointing, ~164 KB on disk, accepted") |
| langgraph-checkpoint-postgres + psycopg[binary] | optional | `durability` | `[binary]` explicit because plain `psycopg` (pulled transitively by the postgres checkpointer) needs system `libpq`, which a fresh CI/dev box lacks (pyproject.toml:57-60) |
| langfuse + openinference-instrumentation-openai-agents + opentelemetry-sdk | optional | `observability` | 3-package group for tracing; opentelemetry-sdk unused in code (see section 2) |
| fastapi + uvicorn | optional | `serve` | HTTP surface only |
| redis | optional (+ duplicated in dev) | `redis` | backs both session adapter and event-log adapter |

No single heaviest-transitive-chain concern beyond what's already extra-gated; the core set itself (openai-agents + langgraph + langchain-core) is the unavoidable heavy baseline for any install.

## 5. Lockfile / metadata

- `uv.lock` exists at repo root (402.5K).
- `[project] name = "agentdeck-sdk"`, import package `agentdeck` - name/import mismatch is intentional (PyPI distribution name vs. import name).
- `requires-python = ">=3.12"`; ruff `target-version = "py312"` (consistent).
- Build backend: hatchling, wheel packages = `["agentdeck"]`.
