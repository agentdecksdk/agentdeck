# Plan — discoverability and adoption

**Status:** proposed · **Date:** 2026-08-12 · **Baseline:** `dev` at `d6a877d`, v3.0.0 released.

The goal, restated so every item below can be tested against it:

> Someone who **does not know AgentDeck exists**, but describes a problem it solves, gets
> **AgentDeck SDK** back as one of the relevant answers.

Not "someone who already knows the name can find it." That distinction decides what is worth
doing: it rules out most logo-and-tagline work and rules in package registries, machine-readable
docs, and problem-shaped pages.

---

## 0. What is already true

Verified 2026-08-12, not assumed. Several items in the source plan are already done or already
blocked, and knowing which changes the order.

| | State |
|---|---|
| **`agentdeck` on PyPI** | **free** — verified via the JSON API. `agentdeck-sdk`, `agentdeck-core`, `agentdecks`, `py-agentdeck` also free |
| **`agentdeck-ai` on PyPI** | **taken, and active** — 7 releases, last 2026-07-13, *"the game console for AI agents"*, unrelated domain |
| **`github.com/agentdeck`** | **held by that same project.** 0 public repos, created 2025-06-09 |
| **`agent-deck` on PyPI** | dead placeholder, *"Your Name"*, one release Aug 2025 |
| **GitHub description** | **done** — leads with "AgentDeck SDK", names the searchable concepts |
| **GitHub topics** | **done** — all twelve from the source plan |
| **Repo homepage URL** | **not set**, deliberately — pointing it at a domain that serves nothing is worse than blank |
| **Social preview image** | **not set** — GitHub renders the default. Every share on X, LinkedIn, Slack and HN uses it |
| **Discussions** | **off** |
| **Wiki** | **on**, and empty — a competing empty surface |
| **PyPI publishing** | **absent.** `release.yml` builds sdist + wheel on tag and stops there |
| **Docs site** | Nextra static export on GitHub Pages under `basePath: /agentdeck`, deployed on release publish |
| **Generated docs** | `scripts/generate_docs_reference.py` already renders settings, CLI and changelog, gate-checked |

## 1. Rulings taken

| # | Ruling | Why |
|---|---|---|
| 1 | **One package: `agentdeck`.** Not `agentdeck-sdk`, and no meta-package | The import name is already `agentdeck` and is frozen — every doc, example and user's code. Given that, the install name must match it, or every user meets `pip install agentdeck-sdk` → `import agentdeck` forever, the papercut `beautifulsoup4`/`bs4` is still famous for. A meta-package would also make every release two releases with a pin to keep true |
| 2 | **Brand is "AgentDeck SDK"; the package is not** | They do different jobs. The brand carries differentiation from `agentdeck-ai` in search results and titles; the package name is typed into a terminal and must agree with the import. `agentdecksdk.com` carries the brand where it counts and nobody else can claim it |
| 3 | **Extensions are extras, not distributions** | `agentdeck[serve]`, `[observability]`, `[durability]` already exist and work. A separate distribution earns its place on exactly three conditions — a different release cadence, a different maintainer, or a dependency that must not even be resolvable — and none is true today. A v3.3 protocol adapter is `agentdeck[a2a]` |
| 4 | **Register nothing speculatively.** No `agentdeck-toolkit`, `-core`, `-sdk` | A PyPI name with no releases is claimable by anyone under PEP 541, so a placeholder protects against nobody while breaching the Terms. `agent-deck` is the worked example: squatted, useless, reclaimable |
| 5 | **The namespace grant is the real mechanism, and it waits** | [PEP 752](https://peps.python.org/pep-0752/) (accepted 2026-06-29) reserves `agentdeck-*` so an impostor's upload fails with `409`; [PEP 755](https://peps.python.org/pep-0755/) (draft) makes it organization-only and requires *"actively using the namespace"*. We qualify strongly on the confusion criterion and not at all on active use until a real `agentdeck-*` exists. Its best value is not defensive — a grant can be **delegated**, which is how a community adapter gets blessed while an impostor gets refused |

## 2. Do now — this week, nothing blocking

Ordered by value per hour. Everything here is reversible and needs no decision.

1. **Publish `agentdeck` to PyPI.** The single highest-value item in the whole plan. `pip install
   agentdeck` is table stakes for being taken seriously, it is the prerequisite for Context7 and
   for every "getting started" a reader will try, and an active project one hyphen away is exactly
   who takes the name next. `release.yml` already builds the artifacts; what is missing is the
   project, Trusted Publishing, and a publish step.
2. **Set a social preview image.** `Settings → General → Social preview`. Every link to this repo
   in X, LinkedIn, Slack, Discord and HN currently renders GitHub's grey default. This is the
   cheapest click-through improvement available and takes two minutes.
3. **Enable Discussions, disable the empty Wiki.** Discussions gives search engines a corpus of
   real questions with the project's name attached — item 34 — and the wiki is a second empty
   surface competing for the same queries.
4. **Merge the project pages** (PR #257): roadmap, known issues, generated changelog. Known
   Issues in particular is the page that makes a project look maintained rather than abandoned.
5. **Write `/llms.txt` and `/llms-full.txt` into the docs build.** Generated, never hand-written —
   `scripts/generate_docs_reference.py` is already the home for exactly this and is gate-checked
   against drift. Items 12–14.

None of these needs the domain, and none needs a decision from anyone.

## 3. The domain cutover — three known blockers

`agentdecksdk.com` is on Cloudflare nameservers and serves nothing. Moving the docs there is not
one change:

- **`basePath: '/agentdeck'`** in `next.config.mjs` exists because Pages serves from a
  subdirectory. On an apex domain it must go, and every internal link changes with it.
- **`test_docs_site_links_in_repo_markdown_reach_a_real_page`** is hardcoded to
  `sagi5060.github.io`. It will fail the moment the README changes, which is correct behaviour and
  needs updating in the same commit.
- **Canonical URLs and redirects.** Two live copies of the docs competing for the same queries is
  worse than one, and is item 19's whole point. GitHub Pages should redirect rather than
  duplicate.

**Sequence:** DNS + CNAME → drop `basePath` → update the pin/link tests → set the repo homepage →
canonical tags → `sitemap.xml` and `robots.txt` → Search Console. Items 1, 17–20.

The Ask AgentDeck backend already answers on `ask.agentdecksdk.com`, so the subdomain half is
proven and the API URL is a build-time variable — the docs move does not disturb it.

## 4. Machine discovery — where this project has an unfair advantage

Items 9–16. Worth doing early, because coding agents are now a real distribution channel and the
supply of well-structured Markdown is small.

- **`llms.txt` / `llms-full.txt`**, generated from the docs build.
- **`context7.json`**, controlling exactly what Context7 indexes.
- **Clean Markdown alongside the rendered site**, so a crawler gets prose rather than a React
  shell — a static export makes this cheap.
- **First paragraphs that define the thing.** Every important page should open with what
  AgentDeck is, what it solves, which frameworks it wraps, and when to use it. This is the single
  highest-leverage prose change, because it is what a retrieval system quotes.

The advantage: the docs are already anti-rot tested, code samples already execute in CI, and the
reference pages are already generated. An LLM-facing corpus built from that is trustworthy in a
way most projects' cannot be.

## 5. Searchable content — the slow, compounding half

Items 21–25 and 30–33. This is where the goal at the top is actually won or lost, and none of it
is mechanical.

**Problem-first guides** matter more than feature pages, because the target reader is searching a
*symptom*: "pause and resume an AI agent", "durable human approval", "resume a workflow from
another process", "share runtime context across agents and tools". Each is a page AgentDeck can
answer honestly and most alternatives cannot.

**Integration and wrapping guides** are the positioning made concrete — *"use your existing
LangGraph agent with AgentDeck"* is the whole thesis in one page title.

**Comparison pages** rank well and are read at decision time. They only work if they are fair:
name what the alternative does better, or the page reads as marketing and is discounted.

**Articles, launch posts, forum answers** (30–33) are the items that actually move the numbers,
and they are the ones no tooling produces. Being findable is necessary and not sufficient.

## 6. Examples — five to ten serious ones

Items 26–29. Five exist today: `chat-agent-with-a-tool`, `workflow-with-an-approval`,
`existing-langgraph-agent`, `agent-with-a-skill`, and `ask-agentdeck` — the last being a real
application, not a demonstration. Every one is exercised by the gate, which is item 28 already
satisfied.

**Amended 2026-08-14.** This paragraph has now been wrong in both directions inside two days, which
is worth recording rather than quietly overwriting. It first listed `agent-with-a-skill` as shipped
when it was not; an amendment on 2026-08-13 corrected that and said #242 was open — and #242 closed
about two hours later, with the example landing in #252 the same afternoon. Both entries were
accurate when written. The lesson is not that either was careless: a hand-maintained inventory of
shipped examples goes stale at the speed the examples ship.
`tests/test_examples.py::test_the_examples_directory_has_not_moved` asserts the four bundle-based
example directories exactly, and `ask-agentdeck` has a suite of its own. **Those tests are the
inventory; this paragraph is a summary of them and will drift again — read it as of its date.**

The gap is the remaining searchable-problem examples: a customer-support agent, a long-running
resumable workflow, a multi-agent system, an MCP-connected agent, one agent served over both HTTP
and chat.

## 7. Measurement

Items 38–40. Define ~30 target questions, run them monthly against Google, GitHub, ChatGPT
Search, Context7 and Perplexity, and record: are we returned, which page, what position, who
appears instead. Track it like a gate — a query that regresses is a defect with a cause.

Worth being honest about the timescale: nothing here shows results in a week. Package registries
and machine-readable docs are indexed in days; search position and community reference take
months and depend mostly on §5, which is writing.

## 8. Sequence

```
NOW      PyPI publish ──► Context7 ──► llms.txt / llms-full.txt
         social preview · Discussions · project pages (#257)
                              │
THEN     domain cutover ──► canonical URLs ──► sitemap/robots ──► Search Console
                              │
THEN     problem-first guides · integration pages · comparisons · /why-agentdeck
                              │
THEN     serious examples ──► articles ──► launch posts ──► forum presence
                              │
ONGOING  measurement, monthly
```

Matching the source plan's own priority: **branding → domain → GitHub metadata → PyPI → README →
core guides → Context7 → llms.txt → integrations → examples → search infrastructure → external
content**, with GitHub metadata pulled forward because it was free and already done, and PyPI
pulled ahead of the domain because it blocks Context7 and needs no DNS.

---

## 9. The contributor loop

Separate from §5–§7, which are about being *found*. This is about what happens to someone who
arrives — and it is a different funnel, because a contributor is acquired through GitHub issue
search rather than through a query about a problem.

```
good first issue → repository → runs an example → contribution → merge → recognition → star / return
```

The step that leaks is **"runs an example"**. Someone arriving from issue search has no idea what
AgentDeck is; without a reason to run it, they make a text edit and leave. Every newcomer issue
therefore carries a block naming *one specific example to run first*, chosen for proximity to that
issue's subject.

### The pool

Keep **5–10 open** at any time, each genuinely finishable in 30 minutes to 3 hours, and prefer
tasks that force the contributor to run the SDK over pure text maintenance. The findings backlog
is the natural source — a finding is already a scoped, reproduced defect with a proposed shape.
Work that is well-defined but too large for a first contribution gets `help wanted` instead.

Do not manufacture easy issues to fill the pool. An artificial issue is discovered as artificial
during the work, and that is a worse first impression than an empty label.

### First merge

The moment a first contribution merges is the only point where recognition is both cheap and
believed. Standard reply:

> Thanks @username — merged. You're now an AgentDeck contributor.
>
> If you found AgentDeck useful, starring the repository helps the SDK reach more developers.
> Happy to have you pick up another `good first issue` or `help wanted` task — and if something
> was confusing while you worked on this, that is worth an issue of its own.

Never a condition, never asked before the merge.

### What CI enforces today, and what it does not

Audited 2026-08-13 against `.github/workflows/ci.yml`, `docs-check.yml` and
`PULL_REQUEST_TEMPLATE.md`.

| Expectation | Enforced? |
|---|---|
| lint · typecheck · import contracts · tests | **yes** — `ci.yml`, on every PR whatever its base |
| a subsystem silently skipping out of the gate | **yes** — the junit-xml skip-reason grep |
| goldens replay in a clean process | **yes** — a second `pytest tests/golden` run |
| docs-site builds and exports | **yes** — `docs-check.yml` |
| docs-site code samples still import what they claim | **yes** — `tests/test_docs_site.py` |
| internal and repo→docs-site links resolve | **yes** — same file |
| every public `Deck` method is documented | **yes** — same file |
| examples still build | **yes** — `tests/test_examples.py` |
| **CHANGELOG entry for a user-visible change** | **no** — checklist only |
| **the PR template's checkboxes** | **no** — nothing parses the PR body |
| **PR opened against `main` instead of `dev`** | **no** — and this is the likeliest newcomer mistake |

The first two are honour-system by design: "user-visible" is a judgement call, and a bot that
demands a CHANGELOG line on a typo fix teaches contributors to tick boxes rather than think. The
third is worth a small check, because it is mechanical, unambiguous, and currently costs a
newcomer a rebase they did not know they needed.

One further thing to know rather than fix: a fork PR from a first-time contributor needs a
maintainer to approve the workflow run before CI reports anything. That silence looks like being
ignored, so approve the run before reviewing.
