# Plan  -  the v3 cutover: ports, engines, runtime, surfaces

**Delivered, phases 0–4** · **Date:** 2026-08-08 · Executes epic Story 2 plus the `authoring/` move it
scopes, against `docs/design/agentdeck-v2-architecture.md` §6's target layout.

## Two rulings taken (2026-08-08)

1. **v1's public API is dropped, not facaded.** `App`, `BaseAgent`, `BaseWorkflow` go; `authoring/` is
   the only way to declare an agent or workflow; `agents/` and `workflows/` are deleted whole. Breaking
   release, migration guide required.
2. **The sandbox becomes a port.** One `core/ports/sandbox.py` + `adapters/caps/sandbox/`, not the fat
   `capabilities.py` the design-doc layout block names  -  see "Sandbox shape".

"Stop depending on old code" is measurable as one package-wide import-linter contract,
`runtime-is-adapter-free`, replacing today's two-module carve-out: `agentdeck.runtime` may not import
`agents`, `langgraph`, `fastapi`, `redis`, `psycopg`, `agentdeck.adapters`, `agentdeck.authoring`,
`agentdeck.skills` or `agentdeck.surfaces`. Green **with no `ignore_imports` exemption** is the
definition of done, which ruling 1 is what makes achievable  -  at v2.x the sandbox needed a carve-out.

**Open question this plan does not answer: v3 has no entry point yet.** Deleting `App` leaves users
with `authoring/` and `build_runtime()`. Decide before phase 4  -  it is the v3 public API that every doc
example and the migration guide are written against. *(Answered by `decision-v3-entry-point.md` →
`plan-phase4-deck.md`.)*

## The actual gap

The v2 adapters exist and are contract-gated, but `app.py:275` wires `v1_engines()`, and
`V1CompatEngine`/`V1CompatWorkflowEngine` **subclass** the real adapters to inject six things the
adapters lack. Those six are the whole migration:

| # | v1 injects | v2 home | phase |
|---|---|---|---|
| 1 | Langfuse observation wrap (`trace_run`) | `LangfuseSink`  -  **already built, unwired** | 1 |
| 2 | Run config: model provider, CA bundle, temperature, max_turns, max_tokens | openai_agents adapter, injected from the composition root | 2 |
| 3 | `usage.reported` per-model-call token aggregation | openai_agents adapter | 2 |
| 4 | Checkpointer laziness (`durable=True` only, keeps `[durability]` optional) | langgraph adapter | 2 |
| 5 | `session_for`  -  one conversation across `App.chat` and HTTP | the adapter's `ExecutionStore` keying | 2 |
| 6 | Shared `Workspace` (three consumers, below) | `core/ports/sandbox.py` + `adapters/caps/sandbox/` | 3 |

**Already retired, do not re-plan:** design doc §6's amendment says the workflow reroute is blocked on
structured state. It is not  -  `RunCompleted.output: Input` and both adapters emit `DataBlock`
(`langgraph/engine.py:203`, `openai_agents/engine.py:213`). Stale; gets a dated correction in phase 5.

## Sandbox shape (phase 3)

| consumer | needs | surface |
|---|---|---|
| openai-agents engine | an opaque handle for `SandboxRunConfig` | passthrough, no port methods |
| `LoadFileNode` (authoring) | `read_text` | port |
| `skills/executor.py` | `read_text`, `write_bytes`, `exec`, env injection | port |

**One `SandboxPort` carrying only the operations actually called**  -  do *not* pre-split into
`FilesystemPort`/`TerminalPort`, because three consumers justify the seam, not the split. Justified by
**DIP, not OCP**: there is one implementation (`UnixLocalSandboxClient`) and CLAUDE.md forbids
interfaces with one implementation, so the exemption is earned by dependency direction  -  three
consumers across three rings import a concrete class out of `runtime/`. A judgment-ledger entry.

## Phases

One integration branch, draft PR from commit 1, `make check` green between phases, merged whole (the
epic's *"do not split this story across releases"*).

| # | Phase | Content and gate |
|---|---|---|
| 0 | cleanup, ~100 lines, no behavior change | Delete `runtime/sessions.py`, `runtime/checkpointer.py`, `agents/mcp/lifecycle.py` (forwarders that invert the ring), `mark_sandbox_tool`, `OpenAISettings.tracing_api_key`; empty `runtime/__init__.py` to a docstring; repoint 6 imports |
| 1 | telemetry cutover  -  cheapest, satisfies an acceptance criterion outright | Wire `LangfuseSink` into `build_runtime(sinks=...)`, remove `trace_run` from both compat engines, retire the inert `runtime_capture`/`current_capture` ambient mechanism (the sink reads identity off the envelope). **Done when** the epic's *"Langfuse traces now cover workflow runs too"* holds and workflow and skill spans carry `session_id`, which they do not today |
| 2 | engine parity  -  load-bearing | Items 2–5 into the adapters, settings resolved at the composition root and injected as `store`/`control` already are; `v1bridge/` deleted here. **Done when** `app.py` wires `OpenAIAgentsEngine`/`LangGraphEngine` directly, the contract suite is green on both, and transcript-fidelity and crash-reconciliation tests are unchanged |
| 3 | sandbox port | `core/ports/sandbox.py` + `adapters/caps/sandbox/`, `runtime/workspace.py` deleted, contract test parametrized over implementations (real + fake) as `tests/contract/` does for engines  -  otherwise a second sandbox silently diverges |
| 4 | `authoring/` **(needs the entry-point decision first)** | `BaseAgent`/`BaseSandboxAgent` → `authoring/agent.py`, `BaseWorkflow` → `authoring/workflow.py`, `SkillNode`/`LoadFileNode`/`AgentNode` → `authoring/nodes.py`, `CapabilitiesSpec` → `authoring/capabilities.py`, each compiling to `InvocableSpec`. **No re-export facades**  -  `agents/` and `workflows/` are deleted, not forwarded |
| 5 | surfaces | `serve.py` → `surfaces/serve/`, `/workflows/*` rerouted through the Runtime (unblocked), `cli.py` → `surfaces/cli/`, dated correction to design doc §6's stale amendment. Last, per the epic's risk note. **Wire default unchanged:** dropping the Python API does not require changing HTTP frames, so `tests/golden/` stays the safety net; a forced frame diff is a deliberate `make golden` with a PR justification, never a silent baseline update |
| 6 | deletion, contract, release | The deletion list below, the package-wide import-linter contract, migration guide, CHANGELOG, version → 3.0.0 |

## Moved vs deleted

**Moved (~1,150 lines)**  -  `agents/base.py`→`authoring/agent.py`, `workflows/base.py`→
`authoring/workflow.py`, `workflows/nodes.py`→`authoring/nodes.py`, `agents/capabilities/`→
`authoring/capabilities.py` + adapters, `runtime/workspace.py`→`adapters/caps/sandbox/`.

**Deleted (~2,000 lines)**  -  `v1bridge/` 311 · `app.py` 513 (composition root only, facade dropped) ·
`agents/runners/` 307 · `serve.py` 301 → `surfaces/serve/` · `runtime/observability.py` 284 →
`adapters/telemetry/langfuse/` · `workflows/runners/` 152 · `agents/registry.py` 21 and
`workflows/registry.py` 21 → `runtime/discovery.py` · forwarders `runtime/checkpointer.py` 30,
`runtime/sessions.py` 12, `agents/mcp/lifecycle.py` 12.

## Risks

- **Phase 2 regresses silently.** Run config is settings-driven, so a dropped field (CA bundle, max_tokens) fails only against a real endpoint. Mitigation: a parity test asserting the adapter's `RunConfig` equals `HeadlessRunner.from_agent`'s, added *before* the move and deleted with `v1bridge/`.
- **v3 removes the facades that made goldens sufficient.** `tests/golden/` covers the SSE wire only, and `tests/test_app.py` covers the Python API being deleted; phase 4 needs equivalent tests against `authoring/` *before* `app.py` goes, or coverage silently drops.
- **The entry-point question blocks phase 4** and is not answered here.
