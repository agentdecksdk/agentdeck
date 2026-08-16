
# AgentDeck v2 — Project Brief

**Owner:** Sagi · **Date:** 2026-08-04 · **Status:** delivered as v3 (amended 2026-08-11)
**Repo:** `Sagi5060/agentdeck` (baseline v1.2.1) · **Doc set:** see `00-project-index.md`

> **Amendment 2026-08-11.** This effort shipped under a different number: ruling 1 of
> `delivery/plan-v2-cutover.md` dropped v1's public API rather than facading it, which made the
> release breaking and renumbered v2 → v3. The *what* and *why* below are unchanged. Two scope
> items did not survive contact, recorded rather than edited out: **caller-injected capabilities**
> and the sandbox behind them are deferred entirely (#163, no sandbox in v3, scaffolding deleted),
> and **ACP** is #129 on `v3.1 — batteries`. Everything else in "one core, one event stream, every
> capability built once" is what `Deck` and the Runtime now are.

## What

Evolve agentdeck from a declarative harness over two agent SDKs (OpenAI Agents SDK, LangGraph)
into an **agent development & runtime platform**: one small core that runs any engine, emits one
canonical event stream, and lets every capability — sessions, streaming chat, pause/resume/cancel,
human approvals, protocols (SSE/ACP, later A2A/A2UI), cost, audit, replay — be built once and work
everywhere.

## Why

The codebase was two parallel silos (agents vs. workflows) with separate registries, runners and
output shapes, so every feature had to be built twice and every new engine or protocol would
multiply that. Agent SDKs churn yearly; the durable assets — conversations, tools, skills,
integrations, dashboards — deserve a home that does not churn with them. The bet: **design the
core that can absorb the platform**, not the platform itself. Its keystone is a versioned event
schema — one log, many readers — plus a `RunContext` threaded through every call from day one.

## How

Three rings with a strict inward dependency rule: a zero-I/O core (nouns + ports), engine and
protocol adapters (each independently deletable), thin surfaces that only render events. Engines
are unified at the lifecycle/event boundary, never at the programming model. Two stores by design
(ADR-D5) — the event log is the platform's record, engine-native session state is each loop's
working memory, and a transcript-fidelity contract test ties them. Safety-net-first: golden wire
baselines (PR #0) and a frozen schema (PR #1) precede any refactor, and a walking skeleton
(Milestone 0) runs three adversarial use cases against the design before the epic is committed.

## Scope

**In:** core schema & context; unified Runtime; both engines as adapters; run control
(pause/resume/cancel + steering); caller-injected capabilities; ACP surface; full backward
compatibility (`.agentdeck/` convention, public API, SSE wire format byte-preserved).
**Next, separate epics:** stdlib/toolkit of tested agents & skills; A2A + A2UI; group sessions
with a pluggable Moderator; advisors; triggers (cron/webhook/log-pattern); eval & replay harness;
operations console.
**Out (refused):** config DSL, auth system in core, marketplace infrastructure, hosted "control
plane," dashboard before the schema stabilizes.

## Success criteria

Adding engine/protocol/consumer #N+1 requires zero changes to core or existing consumers (verified
by diff on the ACP story). The contract-test suite passes identically on both engines. Existing
users notice nothing until they gain new abilities.

The risk concentration was epic Story 2, the seam — mitigated by feature-flagged adapters, the
golden-file serve cutover going last, and shipping in one release. Milestones and their exits live
in `00-project-index.md` §4.
