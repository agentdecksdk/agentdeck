# Plan: AgentDeck docs site

**Reference:** `agentdeck-prd.md` (personas §2, FRs §4, phasing §6, metrics §7) ·
**Baseline:** `docs-site/` as landed in #40 · **Date:** 2026-08-04

## 1. Goal

The docs site is the delivery vehicle for two PRD success metrics — *time-to-first-running-agent
< 5 minutes* and *reference claims system ≤ 100 lines* — and for the PRD's core promise to P1:
"measure the platform by what I did NOT have to write." So the site's job is not coverage, it
is **proof**: every page shows the small amount of user code and names what the platform did
for free. Everything below serves that; anything that doesn't is cut.

Scope boundary: `docs-site/` is the **external** site. `docs/` (PRD, brief, architecture, ADRs,
epics, prompts) stays internal and is never published — different audience, different lifetime.

## 2. Where we are

| Piece | State |
|---|---|
| Stack | Nextra 4 + Next.js App Router, static export, `zod` pinned (see `docs-site/README.md`) — **keep** |
| CI | `docs-check.yml` builds every PR touching `docs-site/` · `docs-pages.yml` deploys on release — **keep** |
| Content | 7 stub pages, ~150 lines total; 5 of them are lists of pages that don't exist |
| Search | Nextra 4 ships Pagefind at build time — unverified, never checked |

Defects to fix before adding anything:

- `content/index.mdx` links to `/docs/getting-started` — 404 (no `/docs` base path).
- `content/cookbook.mdx` and `content/examples.mdx` are absent from `_meta.ts` → unsorted, half-hidden.
- Getting Started tells the reader to `git clone` and `uv pip install -e ".[dev,serve]"`. That is
  a contributor path, not an install, and it makes the <5-minute metric unmeasurable.
- No page contains a code block that anything executes. Docs rot silently today.

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| DS-D1 | Stay on Nextra 4; no migration to Docusaurus/Mintlify/Fumadocs | It builds, it deploys, it searches. Migration buys layout opinions we don't need. |
| DS-D2 | No generated API reference | Public surface is small (`App`, definition bases, capability specs, settings, HTTP). Hand-written pages read better and a Python→MDX pipeline is a permanent maintenance tax. Revisit when FR-24 stdlib adds many entries. |
| DS-D3 | No version switcher; one "latest" site + `Since v2.x` notes on new pages | NFR-1 says v1.2.1 projects keep working. If nothing breaks, snapshots per version are pure cost. |
| DS-D4 | Every Python block in every page is executed by `make check` | The one mechanism that makes the site trustworthy. §6. |
| DS-D5 | Deploy stays release-gated (`docs-pages.yml`) | Published docs describe the released package. `workflow_dispatch` covers the rare preview. |
| DS-D6 | Delete `cookbook.mdx`; recipes are Guides | Two names for task-oriented pages splits the same content twice. |
| DS-D7 | Diagrams only where the mechanism is invisible in prose — event-log fan-out, three rings, run-control safe points. Mermaid in MDX, no image assets | Screenshots and decorative diagrams rot faster than text. |
| DS-D8 | No analytics, no Algolia, no custom theme in phase DS-0/DS-1 | Nothing about them is load-bearing for the metrics in §1. |

## 4. Information architecture

Persona-driven, one section per PRD persona job — not one section per module.

```text
Overview            index.mdx — what you don't have to write, 20-line example, two links
Getting Started     install · first agent · the .agentdeck/ project · first workflow
Concepts            agents · skills · workflows · capabilities · runs & the event log ·
                    sessions & memory · run control · protocols & surfaces
Guides              task-oriented: add a tool · add an MCP server · package a skill ·
                    typed workflow · human approval · serve over HTTP/SSE · durable
                    sessions · providers · deploy
Examples            runnable, one directory per example, mirrors repo examples
Reference           App · definition bases · capability specs · settings (AGENTDECK_*) ·
                    HTTP + SSE wire · event kinds · CLI
Operating           P2 persona (v2.0+): approvals inbox · pause / resume / cancel ·
                    budgets & deadlines · cost per agent/tenant · replay · failure codes
```

`Operating` is the section the PRD implies and the current site has no home for (FR-9–12, FR-22).
It arrives with v2.0, not before — see §7.

**FR → page mapping** (which requirement each page has to make true for a reader):

| PRD | Page |
|---|---|
| FR-1, FR-3 | Getting Started · Concepts/agents · Concepts/capabilities |
| FR-2, FR-13, FR-14 | Concepts/protocols · Guides/serve-http · Reference/HTTP+SSE |
| FR-6 | Concepts/sessions · Guides/durable-sessions |
| FR-7, FR-8, FR-22, NFR-5 | Concepts/runs-and-the-event-log · Reference/event-kinds · Operating/replay |
| FR-9, FR-10, FR-11, FR-12 | Concepts/run-control · Operating/* |
| FR-4, FR-5, FR-23, FR-24 | v2.1 pages (DS-3) |
| FR-15–21, FR-25 | v2.2 / v2.3 pages (DS-4) |
| PRD §8 refusals | Overview, one short "what AgentDeck is not" block |

## 5. Content rules

- One page = one job. If a page needs two H1-sized ideas, it's two pages.
- Every concept page opens with the user code, then what the platform did for free. Prose
  that doesn't attach to code gets cut.
- Requirements live in the PRD, mechanisms in the architecture doc, **usage** on the site.
  The site never restates a requirement or an ADR — no precedence ambiguity with `docs/`.
- Code blocks carry the real import paths and run against scripted fake models (NFR-4:
  no network, no keys) so §6 can execute them.

## 6. Anti-rot machinery (DS-D4)

Two small tests in `tests/`, in `make check`, no new dependency:

- `test_docs_examples.py` — glob `docs-site/content/**/*.mdx`, extract every ` ```python `
  block, run it. A block that must not run is fenced ` ```python no-test ` and the test asserts
  the marker was deliberate. Fails when a rename breaks a documented import.
- `test_docs_links.py` — extract `](/...)` targets from the same files, assert a matching
  `content/` path exists. Catches the current `/docs/getting-started` 404 class of bug.

Both are ~20 lines each. Deliberately not built: a docs coverage metric, a screenshot
pipeline, a prose linter, a timed quickstart harness — the 5-minute claim gets a manual walk
per release, which is once a release, not once a PR.

## 7. Delivery phases

Aligned to PRD §6 releases. DS-0 is the only phase that isn't gated on product work.

### DS-0 — Repair and arm (now, ~1 day, independent of v2)

- [ ] Fix the `/docs/` link; add `cookbook`/`examples` resolution to `_meta.ts` per DS-D6 (delete `cookbook.mdx`)
- [ ] Rewrite Getting Started as a real install path (`pip install agentdeck`) with the smallest agent that runs
- [ ] Land `test_docs_examples.py` + `test_docs_links.py`; both red-tested (break a link and an import on purpose, watch CI fail)
- [ ] Verify Pagefind search works in the static export; if it doesn't, fix or say so in `docs-site/README.md`
- [ ] Overview rewritten: what you don't have to write, one ≤20-line example, "what AgentDeck is not"

### DS-1 — Document what ships today (v1.2.1 truth)

- [ ] Concepts: agents, skills, workflows, capabilities — each with runnable code, each ≤1 screen
- [ ] Guides: add a tool, add an MCP server, package a skill, typed workflow, human approval, serve over HTTP/SSE, durable sessions, providers
- [ ] Reference: `App`, definition bases, capability specs, `AGENTDECK_*` settings, HTTP endpoints, CLI
- [ ] Examples: one page per runnable example, code identical to what's in the repo (asserted by the example test)
- [ ] Done when: a reader with no prior context installs and runs an agent in under 5 minutes on a clean machine (PRD §7), walked manually once

### DS-2 — v2.0 core (gated on epic Stories 2–5)

- [ ] Concepts/runs-and-the-event-log + Reference/event-kinds, generated from the frozen schema's golden JSON so it cannot drift (NFR-5)
- [ ] Concepts/run-control with safe-point semantics (FR-9) and restart-survival (FR-10)
- [ ] `Operating` section: approvals inbox, pause/resume/cancel from any process, budgets & deadlines, structured failure codes, cost, replay
- [ ] Concepts/protocols + ACP guide (FR-14); the epic demo — one agent, three surfaces — becomes one page
- [ ] Migration note: what v1.2.1 users gain, and the explicit "nothing you wrote changed" statement (NFR-1)

### DS-3 — v2.1 batteries

- [ ] Stdlib pages (FR-24): what ships in `agentdeck[toolkit]`, one page per agent/skill, eval status visible
- [ ] `agentdeck new --template` guide; third-party bundle authoring via entry points
- [ ] Eval & replay harness guide (FR-23)

### DS-4 — v2.2 / v2.3 reach

- [ ] A2A serve/consume, A2UI (FR-15/16) · group sessions + Moderator (FR-17/18) · triggers (FR-20)
- [ ] Operations console (FR-25) — documented only once it ships, per PRD's no-dashboard-before-schema-stability refusal

## 8. Success metrics for the site itself

Clean-machine install → running agent in < 5 min (walked per release). 100% of published Python
blocks executed in CI. Zero broken internal links (test, not review). Reference claims-system
example ≤ 100 lines on the page, counted. No page describes behavior that isn't in the released
package — a v2 feature page merges with the release that ships it, not before.

## 9. Out of scope

Blog, changelog mirroring (CHANGELOG.md stays canonical), i18n, a hosted search backend, docs
for internal `core/` internals (that's `docs/design/`), video, and any page for an unshipped
feature. Adding a section requires a persona and an FR — otherwise it's coverage theatre.
