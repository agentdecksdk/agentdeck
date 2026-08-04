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
- No page contains a code block that anything checks. Docs rot silently today.
- **Search is dead.** Nextra's box fetches `_pagefind/pagefind.js` at runtime
  (`nextra/dist/client/components/search.js`, via `addBasePath`), and `next build` never
  produces it — no `postbuild` Pagefind step existed. The box rendered and found nothing.
- The package is **not on PyPI** (private repo, `pyproject.toml` 1.2.1 unpublished), so any
  `pip install agentdeck` instruction is fiction. Interim truth: install from a git tag.
- **`.env` does not work for an installed package** — `runtime/settings.py` loads
  `Path(__file__).parents[2] / ".env"`, which is `site-packages/.env` once installed, and no
  settings class declares `env_file`. It only works in a source checkout, i.e. the path the
  docs now tell readers *not* to take. Docs work around it by exporting the vars; the product
  defect (cwd `.env` never read) is a separate issue, not a docs fix.
- `.env.example` claimed `OPENAI_BASE_URL` "defaults to a legacy private server if unset."
  It does not: `base_url: str = ""` and the packaged `config.default.yaml` documents empty as
  the SDK default (`api.openai.com`). Stale comment from the pre-rename deployment; corrected.

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| DS-D1 | Stay on Nextra 4; no migration to Docusaurus/Mintlify/Fumadocs | It builds, it deploys, it searches. Migration buys layout opinions we don't need. |
| DS-D2 | No generated API reference | Public surface is small (`App`, definition bases, capability specs, settings, HTTP). Hand-written pages read better and a Python→MDX pipeline is a permanent maintenance tax. Revisit when FR-24 stdlib adds many entries. |
| DS-D3 | No version switcher; one "latest" site + `Since v2.x` notes on new pages | NFR-1 says v1.2.1 projects keep working. If nothing breaks, snapshots per version are pure cost. |
| DS-D4 | Every Python block in every page is checked by `make check` — parsed and import-resolved in DS-0, executed from DS-1 | The one mechanism that makes the site trustworthy. §6. |
| DS-D5 | Deploy stays release-gated (`docs-pages.yml`) | Published docs describe the released package. `workflow_dispatch` covers the rare preview. |
| DS-D6 | Delete `cookbook.mdx`; recipes are Guides | Two names for task-oriented pages splits the same content twice. |
| DS-D7 | Diagrams only where the mechanism is invisible in prose — event-log fan-out, three rings, run-control safe points. Mermaid in MDX, no image assets | Screenshots and decorative diagrams rot faster than text. |
| DS-D8 | No analytics, no Algolia, no custom theme in phase DS-0/DS-1 | Pagefind ships with the theme and now works; nothing else here is load-bearing for §1. |
| DS-D9 | Install instructions use a git+tag pin while the repo is private; publishing to PyPI is a product decision, not a docs prerequisite | Documenting `pip install agentdeck` would be the exact fiction §2 flags. Revisit if/when the package is published. |
| DS-D10 | The site owns the *user-facing* contract for run control (safe points, restart semantics); `docs/` owns the internal mechanism, and epic Story 3's AC points at the site page | Otherwise safe-point semantics get two homes and we recreate the precedence ambiguity `00-project-index.md` exists to kill. |

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

Two stages, because the cheap one already has teeth and the expensive one needs content to
justify it.

**Stage 1 — `tests/test_docs_site.py`, in `make check` (DS-0, done).** Globs
`docs-site/content/**/*.mdx`; every `python`/`py` block (indented or not) is `ast.parse`d and
each `agentdeck` import resolved through `importlib` + `getattr`; every absolute markdown link
must have a page; `_meta.ts` keys must match the top-level pages. A block that can't be checked
opts out as ` ```python no-test reason="…" ` — the reason is regex-enforced, so the escape hatch
can't be used silently. ~65 lines, no new dependency, no fixtures. On landing it caught three
real defects: the `/docs/getting-started` 404 and both published examples using `async with` at
module level (never runnable).

**Known ceilings of stage 1** — named because "checked" must not be read as "verified":
non-Python blocks are invisible (a wrong `bash` install or env line passes — exactly how the
`.env` defect below survived); only absolute markdown links are resolved, so relative hrefs,
reference-style links and MDX `<Cards>` are not; anchors are stripped, not validated. Stage 2
closes the first of these for Python only.

**Stage 2 — the example executor (DS-1's opening deliverable, its own line in §7).** Parse
and *run* multi-file examples: fence meta `file=.agentdeck/agents/support/agent.py` writes
into a temp project, ` ```python run ` executes against a fake provider with pinned env.
Cost is honest: the pattern exists (`tests/golden/conftest.py` — monkeypatch
`OpenAIProvider`, pin `_PINNED_ENV`, `chdir` to a fixture project) but
`tests/golden/fake_model.py` is scripted to one two-turn `lookup_slot` conversation, so a
*generalised* scripted provider (per-example scripted turns) has to be built first. Until it
exists, DS-1 pages are import-resolved, not executed — and the plan says so rather than
implying otherwise.

Deliberately not built: a docs coverage metric, a screenshot pipeline, a prose linter, a
timed quickstart harness — the 5-minute claim gets a manual walk per release, which is once
a release, not once a PR.

## 7. Delivery phases

Aligned to PRD §6 releases. DS-0 is the only phase that isn't gated on product work.

### DS-0 — Repair and arm (branch `docs/ds-0-repair`, independent of v2) — **complete**

- [x] Fix the `/docs/` link; `examples` added to `_meta.ts`, `cookbook.mdx` deleted per DS-D6
- [x] Getting Started rewritten: git+tag install (DS-D9), exported provider env, project layout, one-file agent, `run_agent` and `chat`
- [x] `tests/test_docs_site.py` in `make check`; every assertion red-tested (broken link, renamed symbol, reasonless `no-test`, indented/`py`-aliased fence, nav drift)
- [x] Pagefind wired: `postbuild` indexes `out/`, verified under `GITHUB_ACTIONS=true` (6 pages, basePath-correct), CI asserts `out/_pagefind/pagefind.js`, rationale in `docs-site/README.md`
- [x] Overview rewritten: what you did not write, two runnable examples, "what AgentDeck is not" (PRD §8)

Follow-ups DS-0 does not close: the install block pins `@v1.2.1` and nothing gates it, so the
release procedure must bump it (add it to the release skill's checklist); the cwd-`.env` defect
above wants its own issue; nav drift is gated for top-level pages only, not nested `_meta.ts`.

### DS-1 — Document what ships today (v1.2.1 truth)

- [ ] **First:** the example executor of §6 stage 2 — generalised scripted provider + temp-project assembler (`file=` / `run` fence meta). Everything below lands with executed examples once this exists; without it the section's promise is unbuildable
- [ ] Concepts: agents, skills, workflows, capabilities — each with runnable code, each ≤1 screen
- [ ] Guides: add a tool, add an MCP server, package a skill, typed workflow, human approval, serve over HTTP/SSE, durable sessions, providers
- [ ] Reference: `App`, definition bases, capability specs, `AGENTDECK_*` settings, HTTP endpoints, CLI
- [ ] Examples: the pages **are** the source — there is no `examples/` dir in the repo today, so nothing is copied and nothing needs an identity check. If a repo `examples/` dir is ever added, it is generated from these pages, not maintained beside them
- [ ] Done when: a reader with no prior context installs and runs an agent in under 5 minutes on a clean machine (PRD §7), walked manually once

### DS-2 — v2.0 core (gated on epic Stories 2–5)

- [ ] Concepts/runs-and-the-event-log + Reference/event-kinds — a **table generator** over the frozen schema's golden JSON (kind, payload fields, when emitted), nothing more; a generator that grows past a table is the API-reference pipeline DS-D2 refused
- [ ] Concepts/run-control — the canonical user-facing safe-point (FR-9) and restart-survival (FR-10) contract per DS-D10; epic Story 3's AC updated to point here instead of restating it
- [ ] `Operating` section: approvals inbox, pause/resume/cancel from any process, budgets & deadlines, structured failure codes, cost, replay
- [ ] Concepts/protocols + ACP guide (FR-14); the epic demo — one agent, three surfaces — becomes one page
- [ ] Migration note: what v1.2.1 users gain, and the explicit "nothing you wrote changed" statement (NFR-1)

### DS-3 — v2.1 batteries

- [ ] Stdlib pages (FR-24): what ships in `agentdeck[toolkit]`, one page per agent/skill, eval status visible
- [ ] `agentdeck new --template` guide; third-party bundle authoring via entry points
- [ ] Eval & replay harness guide (FR-23)
- [ ] The reference claims system, ≤100 lines on the page and counted (PRD §7) — it lands here because it uses stdlib tools (FR-24), not in DS-1

### DS-4 — v2.2 / v2.3 reach

- [ ] A2A serve/consume, A2UI (FR-15/16) · group sessions + Moderator (FR-17/18) · triggers (FR-20)
- [ ] Operations console (FR-25) — documented only once it ships, per PRD's no-dashboard-before-schema-stability refusal

## 8. Success metrics for the site itself

Clean-machine install → running agent in < 5 min (walked per release). 100% of published Python
blocks checked in CI, and executed once §6 stage 2 lands. Zero broken internal links (test, not
review). Reference claims-system example ≤ 100 lines on the page, counted (DS-3).

The invariant is **the published site describes the released package** — which DS-D5's
release-gated deploy already guarantees. So a feature page may merge with its feature PR; keep
it out of `_meta.ts` nav until release if it should be invisible. No release-day docs crunch.

## 9. Out of scope

Blog, changelog mirroring (CHANGELOG.md stays canonical), i18n, a hosted search backend, docs
for internal `core/` internals (that's `docs/design/`), video, and any page for an unshipped
feature. Adding a section requires a persona and an FR — otherwise it's coverage theatre.
