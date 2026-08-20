# AgentDeck SDK Inventory

Mechanical scan of `agentdeck/` on branch `audit/sdk-report`. Facts only.

## 1. LOC per package

| Package | LOC |
|---|---|
| top-level `agentdeck/*.py` | 2839 |
| core (incl. ports) | 2083 |
| runtime | 2408 |
| adapters | 4947 |
| authoring (incl. runners) | 2111 |
| surfaces | 461 |
| skills | 183 |
| **total** | 15032 |

### Subpackage detail

| Path | LOC |
|---|---|
| core/*.py (no ports) | 1540 |
| core/ports/*.py | 543 |
| adapters/engines | 1764 |
| adapters/stores | 1581 |
| adapters/telemetry | 653 |
| adapters/tools | 541 |
| adapters/leases | 205 |
| adapters/control | 198 |
| authoring/*.py (no runners) | 1830 |
| authoring/runners | 281 |

### 5 largest files

| File | LOC |
|---|---|
| `agentdeck/deck.py` | 1413 |
| `agentdeck/runtime/service.py` | 951 |
| `agentdeck/runtime/settings.py` | 636 |
| `agentdeck/adapters/engines/langgraph/engine.py` | 500 |
| `agentdeck/adapters/stores/postgres/store.py` | 476 |

## 2. Public API surface

### `agentdeck/__init__.py` (top-level)

`Agent`, `AgentdeckError`, `ConfigError`, `Context`, `ContextTypeError`, `Deck`, `NotFoundError`, `Run`, `SessionBusyError`, `SkillError`, `StoreError`, `TurnResult`, `Workflow`, `__version__`

No internal-looking names (stores/resolvers/contexts/log keys) at this top level.

### `agentdeck/core/__init__.py`

Event schema, content blocks, `RunContext`, `InvocableKind`/`InvocableSpec`, `Reporter`, `RunStatus`/`can_resume`/`status_of`. All schema-level, nothing internal.

### `agentdeck/core/ports/__init__.py`

`ControlPort`, `EnginePort`, `EventSinkPort`, `EventStorePort`, `LeasePort`, `RunSummary`, `SessionClaim`, `ToolSet`, `ToolSourcePort` -- ports package, exports are its whole point.

### `agentdeck/authoring/__init__.py`

`Agent`, `AgentDeclaration`, `AgentNode`, `InterruptResult`, `LoadFileNode`, `Workflow`, `WorkflowDeclaration`, `sleep_until`

### `agentdeck/authoring/runners/__init__.py`

`BaseRunner`, `BaseWorkflowRunner`, `DevWorkflowRunner`, `HeadlessRunner`, `StreamDone`

### `agentdeck/skills/__init__.py`

`Skills`, `SkillBundle`

### `agentdeck/surfaces/__init__.py`, `surfaces/cli`, `surfaces/serve`

`surfaces/cli`: `render`, `stream_chat`. `surfaces/serve`: `build_app`. No internal names.

### `agentdeck/runtime/__init__.py`

No exports, no `__all__` -- docstring only.

### Adapter package exports (internal-looking names flagged)

| Package | Exports | Internal-looking |
|---|---|---|
| `adapters/control/memory`, `adapters/control/sqlite` | `MemoryControlPort`, `SqliteControlPort` | none |
| `adapters/leases/memory`, `adapters/leases/sqlite` | `MemoryLeasePort`, `SqliteLeasePort` | none |
| `adapters/stores/{memory,postgres,redis,sqlite}` | one `*EventStore` class each | none (store adapters are meant to export a store) |
| `adapters/engines/langgraph` | `DURABLE_KEY`, `REPORTER_KEY`, `LangGraphEngine`, `resolve_checkpointer` | `DURABLE_KEY`, `REPORTER_KEY` are context/log keys exported at package level |
| `adapters/engines/openai_agents` | `ExecutionStore`, `OpenAIAgentsEngine`, `RunSettings`, `SessionFactory` | `ExecutionStore` is store-named internal state |
| `adapters/engines/stub` | `Step`, `StubEngine`, `stub_spec` | none |
| `adapters/telemetry/langfuse` | `MAX_OPEN_CALLS`, `MAX_OPEN_RUNS`, `LangfuseSink`, `LangfuseTracer`, `Level`, `Observation`, `ObservationKind`, `Tracer`, `build_client`, `langfuse_sink` | `MAX_OPEN_CALLS`/`MAX_OPEN_RUNS` are internal tuning constants exported at package level |
| `adapters/tools/mcp` | `MCP_SERVER_NAMES_KEY`, `MCPLifecycle`, `MCPServerStreamableHttpResilient`, `MCPToolSource`, `mcp_status_banner`, `resolve_agent_mcp_servers`, `resolve_agent_mcp_status` | `MCP_SERVER_NAMES_KEY` is a context/log key; `MCPLifecycle` and two `resolve_*` functions are resolver/lifecycle internals exported at package level |

## 3. Debt markers

| Category | Count |
|---|---|
| TODO | 1 |
| FIXME | 0 |
| HACK | 0 |
| XXX | 0 |
| `ponytail:` | 12 |
| `type: ignore` | 0 |
| `noqa` | 7 |

### TODO

- `agentdeck/adapters/engines/openai_agents/translate.py:102: # TODO(#52): a non-function tool call (computer-use, MCP approval) never populates`

### `ponytail:`

- `agentdeck/runtime/service.py:184: # ponytail: whole log per run  -  window it (or hand the engine a summary) once a`
- `agentdeck/runtime/service.py:508: # ponytail: an asyncio renewer cannot run while the event loop is blocked, so a tool`
- `agentdeck/runtime/service.py:742: # ponytail: every parked run's whole log, per call, and an approval inbox polls this  -`
- `agentdeck/adapters/control/sqlite/port.py:23: # ponytail: the signals table grows one row per signaled run, never pruned  -  add a`
- `agentdeck/adapters/control/sqlite/port.py:82: # ponytail: silent, like the event store's  -  log it if an operator ever has to`
- `agentdeck/core/reporting.py:42: # ponytail: drained between payloads, not concurrently with them  -  a report emitted during one`
- `agentdeck/adapters/stores/sqlite/store.py:132: # ponytail: the degraded mode is invisible  -  log it if an operator ever has to find out`
- `agentdeck/adapters/engines/langgraph/checkpointer.py:64: half is a saver that works on the second loop. Zero effect on a server. ponytail: bounding`
- `agentdeck/adapters/engines/openai_agents/reconcile.py:63: # ponytail: whole log against whole session, every turn  -  the same ceiling the Runtime's`
- `agentdeck/adapters/leases/sqlite/port.py:30: # ponytail: one row per leased run, deleted on release  -  a killed worker's row is the only`
- `agentdeck/adapters/engines/openai_agents/sessions.py:107: # ponytail: SQLiteSession.close() is sync and cheap for the :memory:/local-file`
- `agentdeck/runtime/discovery.py:34: # ponytail: one engine per kind, forever  -  the day a second engine plays one shape, the`

### `noqa`

- `agentdeck/adapters/engines/langgraph/checkpointer.py:97: except BaseException as exc:  # noqa: BLE001`
- `agentdeck/adapters/engines/langgraph/checkpointer.py:163: conn._thread.daemon = True  # noqa: SLF001`
- `agentdeck/adapters/engines/openai_agents/reconcile.py:145: if role == "user" or role == "assistant":  # noqa: PLR1714`
- `agentdeck/core/control.py:55: class ControlSignalled(Exception):  # noqa: N818`
- `agentdeck/core/events.py:39: from agentdeck.core.content import Input  # noqa: TC001`
- `agentdeck/core/ports/sink.py:24: async def start(self) -> None:  # noqa: B027`
- `agentdeck/core/ports/sink.py:44: async def close(self) -> None:  # noqa: B027`

## 4. Oversized / dead

### Files > 800 LOC

| File | LOC |
|---|---|
| `agentdeck/deck.py` | 1413 |
| `agentdeck/runtime/service.py` | 951 |

### Never-imported modules

None found. Every non-`__init__.py` module under `agentdeck/` (73 modules) has at least one reference to its dotted path elsewhere in the repo.
