# SDK Audit: Execution Plan

Goal: honest senior-engineer assessment of the AgentDeck SDK. The good, the bad, the best, the worst.
Every finding carries evidence: a snippet, a `file:line` ref, and an external reference where relevant.
No cap on finding count. Everything relevant goes in.

Working copy: `/home/sagi5060/prjs/agentdeck-audit` @ branch `audit/sdk-report` (from `origin/dev` @ 3091975).

Rule: a checkbox is ticked only when the evidence column is filled. Progress lives in this file, not in anyone's memory.

## Phase 0: Setup

| Done | Step | Evidence |
|------|------|----------|
| [x] | Worktree from latest dev | `git worktree add ../agentdeck-audit -b audit/sdk-report origin/dev`, HEAD=3091975 |
| [x] | Plan written | this file |

## Phase 1: Mechanical scouts (sonnet, parallel, cheap)

Each scout writes raw findings to `reports/_scout/<name>.md`. No opinions, only facts. `_scout/` files are working evidence for the analysts, not part of the deliverable; VERDICT never links to them.

| Done | Scout | Scope | Evidence (output file) |
|------|-------|-------|------------------------|
| [x] | inventory | LOC per package, public API surface (`__init__` exports), dead/oversized modules, TODO/FIXME/ponytail markers | reports/_scout/inventory.md (15032 LOC; deck.py 1413 + service.py 951 oversized; adapter-level export leaks; 12 ponytail markers) |
| [x] | deps | pyproject deps + extras vs actual imports, unused/heavy deps, version pins, supply-chain notes | reports/_scout/deps.md (opentelemetry-sdk declared never imported; aiosqlite imported undeclared; all optional imports lazy) |
| [x] | tests | test count per area, coverage gaps (SDK modules with no test file), golden/snapshot hygiene, slow/skipped tests | reports/_scout/tests.md (21K test LOC, flat layout; 5 coverage gaps incl. runners/workflow.py and mcp/transport.py; 9 documented skips, 0 xfail) |
| [x] | drift | README + docs/engineering claims vs code reality, CHANGELOG vs shipped API, stale examples | reports/_scout/drift.md (zero drift: README, engineering docs, examples, CHANGELOG all verified against code) |

## Phase 2: Area analysts (opus, parallel, judgment)

Each analyst reads its area (excerpts, not whole-file dumps) plus the relevant scout files, and writes its report directly. Format per finding: verdict line, snippet, `file:line`, external ref where relevant. Good AND bad in the same file.

| Done | Report | Scope | Evidence (report file) |
|------|--------|-------|------------------------|
| [x] | 01-architecture.md | layering (core/runtime/adapters/authoring/surfaces), import boundaries, composition root, `deck.py` at 72KB | reports/01-architecture.md (7 GOOD / 18 BAD, 2 high) |
| [x] | 02-api-design.md | public API ergonomics: `Deck`, `run`, authoring declarations, error taxonomy, typing discipline | reports/02-api-design.md (12 GOOD / 20 BAD; headline: dict-typed outputs, bare RuntimeError vs AgentdeckError contract) |
| [x] | 03-runtime.md | lifecycle state machine, event dispatch, cancellation, concurrency, persistence contracts | reports/03-runtime.md (26 GOOD / 18 BAD) |
| [x] | 04-adapters.md | engines (openai_agents, langgraph), stores (sqlite/postgres/redis), mcp, telemetry: isolation, parity, quality | reports/04-adapters.md (14 GOOD / 25 BAD, 3 high: control/lease backend parity gap, unescaped session key, langgraph zero token usage) |
| [x] | 05-testing.md | test quality (not just count): doubles, goldens, flakiness risk, what a regression would slip past | reports/05-testing.md (12 GOOD / 13 BAD) |
| [x] | 06-docs-dx.md | onboarding path, docs-site, examples, error-message quality, time-to-first-agent | reports/06-docs-dx.md (12 GOOD / 16 BAD; headline: 15/33 docs-site stub pages contradict code, Quickstart cannot run as written; contradicts drift scout's zero-drift claim, resolve in Phase 3) |
| [x] | 07-security-deps.md | trust boundaries, secrets handling, serve surface (HTTP/SSE), dependency risk | reports/07-security-deps.md (12 GOOD / 16 BAD) |

## Phase 3: Synthesis (main agent)

| Done | Step | Evidence |
|------|------|----------|
| [x] | Read all reports, cross-check contested claims against code | Spot-checked 7 headline claims, all verified: sessions.py:101 unescaped key, engine.py:389 zero usage, deck.py:1295 bare RuntimeError, deck.py:878 `TurnResult \| Any`, serve.py:350 `0.0.0.0`, quickstart.mdx has zero `asyncio.run`, deck.py:48 eager engine imports. Drift-scout vs docs-DX contradiction resolved: different scopes, both correct (README/engineering clean; docs-site stubs were outside the drift verification set). |
| [x] | VERDICT.md: exec summary, the best, the worst, one-liners linking into area files | reports/VERDICT.md (221 findings total: 95 GOOD / 126 BAD) |
| [ ] | Commit + push branch | commit SHA |

## Token discipline

- Scouts: sonnet. Analysts: opus. Never fable for subagents.
- One pass per area. No re-sweeps unless synthesis finds a contradiction.
- Agents read excerpts and grep; no full-file dumps into context.
