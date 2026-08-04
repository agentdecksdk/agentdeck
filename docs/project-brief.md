# AgentDeck v2 — Project Brief

**Owner:** Sagi · **Date:** 2026-08-04 · **Status:** design complete, execution starting
**Repo:** `Sagi5060/agentdeck` (baseline v1.2.1) · **Doc set:** see `00-project-index.md`

## What

Evolve agentdeck from a declarative harness over two agent SDKs (OpenAI Agents SDK,
LangGraph) into an **agent development & runtime platform**: one small core that runs
any engine, emits one canonical event stream, and lets every capability — sessions,
streaming chat, pause/resume/cancel, human approvals, protocols (SSE/ACP, later
A2A/A2UI), cost, audit, replay — be built once and work everywhere.

## Why

Today the codebase is two parallel silos (agents vs. workflows) with separate
registries, runners, and output shapes. Every feature must be built twice; every new
engine or protocol would multiply that. Meanwhile agent SDKs churn yearly. The durable
assets — conversations, tools, skills, integrations, dashboards — deserve a home that
does not churn with them. The strategic bet: **design the core that can absorb the
platform**, not the platform itself. The keystone is a versioned event schema — one log,
many readers — plus a RunContext threaded through every call from day one.

## How (approach in one paragraph)

Three rings with a strict inward dependency rule: a zero-I/O core (nouns + ports), engine
and protocol adapters (each independently deletable), thin surfaces that only render
events. Engines are unified at the lifecycle/event boundary, never at the programming
model. Two stores by design (ADR-D5): the event log is the platform's record; engine-
native session state is each loop's working memory; a transcript-fidelity contract test
ties them. Execution is safety-net-first: golden wire baselines (PR #0) and a frozen
schema (PR #1) precede any refactor; a 1–2 week walking skeleton (Milestone 0) runs
three adversarial use cases against the design before the full epic is committed.

## Scope

**In (this initiative):** core schema & context; unified Runtime; both engines as
adapters; run control (pause/resume/cancel + steering); caller-injected capabilities;
ACP surface; full backward compatibility (`.agentdeck/` convention, public API, SSE wire
format byte-preserved).
**Next (separate epics, already specified):** stdlib/toolkit of tested agents & skills;
A2A + A2UI; group sessions with a pluggable Moderator; advisors; triggers (cron/webhook/
log-pattern); eval & replay harness; operations console.
**Out (refused):** config DSL, auth system in core, marketplace infrastructure, hosted
"control plane," dashboard before the schema stabilizes.

## Milestones

| # | Milestone | Exit |
|---|---|---|
| 0 | PR #0 baselines + guardrails | goldens committed, linter red-tested |
| 1 | PR #1 event schema v1 | schema frozen, forward-compat proven |
| 2 | Walking skeleton (UC1 handoff chat · UC2 kill/restart approval · UC3 cross-process cancel) | recorded demo, zero falsifiers fired, findings note |
| 3 | Epic Stories 2–5 (seam → control → capabilities → ACP) | one unmodified agent on three surfaces; old silos deleted |

## Success criteria

Adding engine/protocol/consumer #N+1 requires zero changes to core or existing
consumers (verified by diff on the ACP story). Contract-test suite passes identically on
both engines. Existing users notice nothing until they gain new abilities.

## Top risks

Story 2 (the seam) is the size/risk concentration — mitigated by feature-flagged
adapters, golden-file serve cutover last, single-release shipping. Schema mistakes are
forever — mitigated by review-as-PR, golden JSON per kind, and Milestone 0 existing
specifically to falsify it cheaply. Solo-developer bus factor — mitigated by this doc
set being executable (handoff prompts per PR).
