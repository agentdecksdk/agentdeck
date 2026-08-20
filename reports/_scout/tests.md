# Scout: tests/

Facts only. No opinions. Working evidence for area analysts; not linked from VERDICT.

## 1. Structure: file count / LOC per directory, mirror of agentdeck/

| tests dir | files (.py) | LOC | mirrors agentdeck/ |
|---|---|---|---|
| tests/ (flat, top-level) | 70 | 21164 | adapters, authoring, core, runtime, surfaces, skills all mixed together, one file per feature/use-case (e.g. `test_uc1_handoff.py`, `test_uc2_claim_pipeline.py`) rather than one dir per package |
| tests/contract/ | 15 | 2920 | port-conformance suite: parametrized cases run against every store/engine/lease implementation together (`contract_cases.py`, `langgraph_cases.py`, `openai_agents_cases.py`) |
| tests/core/ | 12 | 1921 | agentdeck/core/ (events, content, context, control, reporting, import-law) |
| tests/golden/ | 2 top-level (+ fixture_project + snapshots dirs) | 195 | end-to-end HTTP/SSE snapshot tests against `surfaces/serve` |

No `tests/adapters/`, `tests/authoring/`, `tests/runtime/`, or `tests/surfaces/` directories exist. The flat top-level `tests/` (70 files) is where adapters/authoring/runtime/surfaces coverage actually lives, addressed by filename convention (`test_openai_agents_engine.py`, `test_langgraph_*.py`, `test_sqlite_store.py`, `test_redis_store.py`, `test_postgres_store.py`, `test_serve*.py`) rather than directory structure.

Import-reference counts (files under `tests/` containing `from agentdeck.<pkg>` for each top-level package):

| agentdeck package | LOC | files | test files referencing it |
|---|---|---|---|
| adapters | 4947 | 43 | 56 |
| authoring | 2111 | 18 | 38 |
| core | 2083 | 16 | 75 |
| runtime | 2408 | 7 | 44 |
| surfaces | 461 | 7 | 7 |
| skills | 183 | 2 | 2 |

## 2. Coverage gaps: agentdeck modules >50 LOC with no test file and no import/symbol reference in tests/

Checked all 65 agentdeck modules >50 LOC against: (a) a matching `tests/test_<basename>.py`, (b) any `agentdeck.<dotted.path>` reference in tests/, (c) top-level `from agentdeck import <name>`, then hand-verified ambiguous cases by grepping for the actual class/function names the module defines (package-level re-exports mean many modules are exercised without their submodule path ever appearing in a test file).

Confirmed gaps, no test file and no symbol reference anywhere in tests/:

| Module | LOC | Symbols checked | Note |
|---|---|---|---|
| `agentdeck/authoring/runners/workflow.py` | 99 | `BaseWorkflowRunner`, `DevWorkflowRunner` | zero hits for either name in tests/ |
| `agentdeck/adapters/tools/mcp/wiring.py` | 63 | `resolve_agent_mcp_status`, `resolve_agent_mcp_servers` | zero hits; sibling function `mcp_status_banner` from the same file IS tested (tests/test_mcp_tool_source.py) |
| `agentdeck/adapters/tools/mcp/transport.py` | 234 | module path / class names | zero direct reference; only reached (if at all) via `from agentdeck.adapters.tools.mcp import (...)` package-level import in test_mcp_tool_source.py, not confirmed to exercise this file |
| `agentdeck/adapters/telemetry/langfuse/client.py` | 180 | `build_client` | tests/test_observability.py:4 docstring states the module is stubbed at its "one construction seam" (`build_client`) deliberately, so assertions hold "without the `[observability]` extra... without a network" -  not exercised for real |
| `agentdeck/skills/bundle.py` | 65 | `SkillBundle` | zero direct symbol reference; tests/test_skills.py exercises bundles only through the `Skills(...).list()/.build()` facade, not this class directly |

Modules that looked like gaps under the mechanical import-path check but are covered once package-level re-exports are followed (false positives, listed for the record): `agentdeck/__init__.py` (re-exports `Deck`/`Agent`/`Workflow` heavily used), `agentdeck/authoring/agent.py` (`Agent` class), `agentdeck/authoring/nodes.py` (`AgentNode` in test_observability.py/test_workflow_streaming.py, `LoadFileNode` has its own test_load_file_node.py), `agentdeck/authoring/workflow.py` (`Workflow` class), `agentdeck/adapters/control/sqlite/port.py` (`SqliteControlPort`), `agentdeck/adapters/leases/sqlite/port.py` (`SqliteLeasePort`), `agentdeck/adapters/leases/memory/port.py` (`MemoryLeasePort`), `agentdeck/adapters/engines/stub/engine.py` (`StubEngine`/`stub_spec`, used across 10 test files), `agentdeck/core/ports/engine.py` (`EnginePort`), `agentdeck/core/ports/sink.py` (`EventSinkPort`).

## 3. Grep: skip / xfail / sleep / time.time / flaky

No `xfail` anywhere in tests/. No `time.time` calls in tests/.

`pytest.skip` / `pytest.mark.skip` hits:

| file:line | reason |
|---|---|
| tests/contract/test_resume.py:30 | "only a suspended case can be resumed" |
| tests/contract/test_event_stream.py:46 | "a suspended run's terminal event arrives on resume" |
| tests/contract/test_event_stream.py:52 | "this run finished" |
| tests/contract/live_stores.py:58 | Postgres event log needs psycopg with libpq |
| tests/contract/live_stores.py:215 | store target unavailable (parametrized skip) |
| tests/contract/live_stores.py:220 | generic unavailable reason |
| tests/core/test_old_reader_block_compat.py:70 | old reader cannot read at ref, so nothing to measure |
| tests/core/test_old_reader_compat.py:38 | module-level `pytestmark = pytest.mark.skip(...)`, reason: envelope v2 removed required `tenant`, "v1 reader cannot parse a v2 event by construction" |
| tests/core/test_old_reader_compat.py:63 | same per-test skip pattern as above |

`flaky` hits (all deliberate test-double naming, not markers):

| file:line | what |
|---|---|
| tests/test_sink_dispatch.py:89 | `class Flaky(EventSinkPort)` -  a sink double that fails |
| tests/test_sink_dispatch.py:386, 630, 656 | `Flaky()` instantiated in test bodies |
| tests/test_deck.py:2109, 2115, 2122 | `session_id="t-flaky"`, `_flaky_tick` monkeypatch helper |

`sleep(` / `asyncio.sleep(` hits with value >= 0.5s:

| file:line | value |
|---|---|
| tests/test_sink_dispatch.py:826 | 0.5 |
| tests/test_sink_dispatch.py:1030 | 0.5 |
| tests/contract/test_store.py:322, 335 | `AGE_GAP` = 0.5 (constant defined tests/contract/test_store.py:190) |

All other `asyncio.sleep`/`time.sleep` calls in tests/ (concurrency_worker.py, crash_worker.py, test_crash_reconciliation.py, test_runtime_service.py, test_uc2/uc3, test_workflow_timers.py, etc.) are <=0.3s, mostly 0 or sub-0.05s yield-points.

## 4. golden/ and core/snapshots/: counts, sizes, last-modified spread

| Dir | file count | size | file type |
|---|---|---|---|
| tests/golden/snapshots/ | 18 | 72K | all `.http` (numbered `01_health.http` .. `18_*.http`) |
| tests/core/snapshots/ | 21 | 84K | all `.json` (one per core event kind, e.g. `run.completed.json`, `control.requested.json`) |

Last-modified sample (git log -1 --format=%cs), first 10 files of each dir:

| tests/golden/snapshots/ | date | tests/core/snapshots/ | date |
|---|---|---|---|
| 01_health.http | 2026-08-13 | artifact.created.json | 2026-08-10 |
| 02_chat.http | 2026-08-04 | control.observed.json | 2026-08-10 |
| 03_chat_stream.http | 2026-08-04 | control.requested.json | 2026-08-10 |
| 04_chat_missing_field.http | 2026-08-04 | custom.json | 2026-08-10 |
| 05_agent_unknown.http | 2026-08-04 | input.appended.json | 2026-08-10 |
| 06_workflow.http | 2026-08-04 | message.completed.json | 2026-08-10 |
| 07_workflow_stream.http | 2026-08-04 | node.updated.json | 2026-08-10 |
| 08_interrupt_stream.http | 2026-08-04 | progress.reported.json | 2026-08-10 |
| 09_pending.http | 2026-08-04 | run.cancelled.json | 2026-08-10 |
| 10_resume.http | 2026-08-04 | run.completed.json | 2026-08-10 |

core/snapshots/ sample is a single same-day batch (2026-08-10). golden/snapshots/ sample is one file updated 2026-08-13, the rest from 2026-08-04.

## 5. Test doubles: Fake / Stub / InMemory classes

`class InMemory*` -  no hits anywhere in tests/.

| Class | file:line | Doubles |
|---|---|---|
| `FakeClock` | tests/test_run_control.py:64 | a monotonic clock callable, advanced by hand for cooldown/timeout arithmetic in run-control tests |
| `FakeClock` | tests/test_sink_dispatch.py:26 | same pattern, separate copy, for sink-dispatch cooldown tests |
| `FakeRunResultStreaming` | tests/test_streaming.py:76 | OpenAI Agents SDK's streaming result object |
| `FakeRunResultStreaming` | tests/test_workflow_streaming.py:142 | same, workflow-streaming variant |
| `Stubborn(Buffering)` | tests/test_sink_dispatch.py:251 | subclasses the real `Buffering` event-sink base to model a consumer that keeps running inside `emit` past a reap deadline |
| `StubSaver` | tests/test_workflow_durability.py:196 (local class, inside a test function) | LangGraph checkpointer's `AsyncPostgresSaver` instance returned by `.setup()` |
| `StubAsyncPostgresSaver` | tests/test_workflow_durability.py:205 (local class) | LangGraph's `AsyncPostgresSaver.from_conn_string` classmethod, monkey-injected via `sys.modules` |

Two independent `FakeClock` implementations exist (test_run_control.py and test_sink_dispatch.py), not shared.

No fake/stub Store or Engine port class found under this exact naming convention: real production doubles used for that role are `agentdeck.adapters.stores.memory.store.MemoryStore` and `agentdeck.adapters.engines.stub.engine.StubEngine`, both shipped in `agentdeck/` itself (not test-local fakes).

## 6. conftest.py inventory

| Path | Notable fixtures / hooks |
|---|---|
| tests/conftest.py | `pytest_terminal_summary` hook: prints docs-examples run/illustrative counts after `-q` runs. Autouse fixture `_release_the_deck_claim`: force-releases a Deck's process claim after every test via `sys.modules` lookup (avoids importing `agentdeck.deck` in core-only tests) |
| tests/contract/conftest.py | `case` (parametrized over `CASES`, id'd by case.id), `ctx` (`RunContext`), `store` (`MemoryEventStore`), `runtime` (`Runtime`, depends on case+store), `played` (async, depends on case+runtime+ctx) -  the parametrization spine for the contract/conformance suite |
| tests/core/conftest.py | session-scoped `examples` (dict of built `Event`s) and `make_event` factory fixture, plus helpers `_event`/`examples_from` |
| tests/golden/conftest.py | `_golden_model` (`ScriptedModel` factory) and `make_client` fixture (monkeypatch-based HTTP client factory) for the HTTP/SSE snapshot suite |

Four conftest.py files total: tests/, tests/contract/, tests/core/, tests/golden/. None under a would-be tests/adapters/ or tests/runtime/ since those directories don't exist.
