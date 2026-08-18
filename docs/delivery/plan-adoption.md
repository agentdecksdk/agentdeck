# Plan  -  discoverability and adoption

**Status:** proposed · **Date:** 2026-08-12 · **Baseline:** `dev` at `d6a877d`, v3.0.0 released.

> Someone who **does not know AgentDeck exists**, but describes a problem it solves, gets
> **AgentDeck SDK** back as one of the relevant answers.

Not "someone who already knows the name can find it." That distinction rules out most
logo-and-tagline work and rules in package registries, machine-readable docs, and problem-shaped
pages.

## 0. What is already true

Verified 2026-08-12.

| | State |
|---|---|
| **`agentdeck` on PyPI** | **free**  -  verified via the JSON API. `agentdeck-sdk`, `agentdeck-core`, `agentdecks`, `py-agentdeck` also free |
| **`agentdeck-ai` on PyPI** | **taken, and active**  -  7 releases, last 2026-07-13, *"the game console for AI agents"*, unrelated domain |
| **`github.com/agentdeck`** | **held by that same project.** 0 public repos, created 2025-06-09 |
| **`agent-deck` on PyPI** | dead placeholder, *"Your Name"*, one release Aug 2025 |
| **GitHub description** | **done**  -  leads with "AgentDeck SDK", names the searchable concepts |
| **GitHub topics** | **done**  -  all twelve from the source plan |
| **Repo homepage URL** | **not set**, deliberately  -  pointing it at a domain that serves nothing is worse than blank |
| **Social preview image** | **not set**  -  GitHub renders the default. Every share on X, LinkedIn, Slack and HN uses it |
| **Discussions** | **off** |
| **Wiki** | **on**, and empty  -  a competing empty surface |
| **PyPI publishing** | **absent.** `release.yml` builds sdist + wheel on tag and stops there |
| **Docs site** | Nextra static export on GitHub Pages under `basePath: /agentdeck`, deployed on release publish |
| **Generated docs** | `scripts/generate_docs_reference.py` already renders settings, CLI and changelog, gate-checked |

## 1. Rulings taken

| # | Ruling | Why |
|---|---|---|
| 1 | **One package: `agentdeck`.** Not `agentdeck-sdk`, and no meta-package | The import name is already `agentdeck` and is frozen, so the install name must match it or every user meets `pip install agentdeck-sdk` → `import agentdeck` forever  -  the papercut `beautifulsoup4`/`bs4` is still famous for. A meta-package would also make every release two releases with a pin to keep true |
| 2 | **Brand is "AgentDeck SDK"; the package is not** | The brand carries differentiation from `agentdeck-ai` in search results and titles; the package name is typed into a terminal and must agree with the import. `agentdecksdk.com` carries the brand where it counts |
| 3 | **Extensions are extras, not distributions** | `agentdeck[serve]`, `[observability]`, `[durability]` already work. A separate distribution earns its place on three conditions  -  different release cadence, different maintainer, or a dependency that must not even be resolvable  -  and none is true. A v3.3 protocol adapter is `agentdeck[a2a]` |
| 4 | **Register nothing speculatively.** No `agentdeck-toolkit`, `-core`, `-sdk` | A PyPI name with no releases is claimable by anyone under PEP 541, so a placeholder protects against nobody while breaching the Terms. `agent-deck` is the worked example: squatted, useless, reclaimable |
| 5 | **The namespace grant is the real mechanism, and it waits** | [PEP 752](https://peps.python.org/pep-0752/) (accepted 2026-06-29) reserves `agentdeck-*` so an impostor's upload fails with `409`; [PEP 755](https://peps.python.org/pep-0755/) (draft) makes it organization-only and requires *"actively using the namespace"*, which we do not until a real `agentdeck-*` exists. Its best value is not defensive  -  a grant can be **delegated**, which is how a community adapter gets blessed while an impostor gets refused |

## 2. Do now  -  this week, nothing blocking

Ordered by value per hour; everything here is reversible and needs no decision.

| # | Do | Why |
|---|---|---|
| 1 | **Publish `agentdeck` to PyPI** | The highest-value item in the plan: prerequisite for Context7 and for every "getting started" a reader will try, and an active project one hyphen away is exactly who takes the name next. `release.yml` already builds the artifacts; missing are the project, Trusted Publishing, and a publish step |
| 2 | **Set a social preview image** (`Settings → General → Social preview`) | Every link in X, LinkedIn, Slack, Discord and HN currently renders GitHub's grey default |
| 3 | **Enable Discussions, disable the empty Wiki** | Discussions gives search engines a corpus of real questions with the project's name attached (item 34); the wiki competes for the same queries with nothing in it |
| 4 | **Merge the project pages** (PR #257): roadmap, known issues, generated changelog | Known Issues is the page that makes a project look maintained rather than abandoned |
| 5 | **Write `/llms.txt` and `/llms-full.txt` into the docs build** | Generated from `scripts/generate_docs_reference.py`, never hand-written. Items 12–14 |

## 3. The domain cutover  -  three known blockers

`agentdecksdk.com` is on Cloudflare nameservers and serves nothing. Moving the docs there is not one
change:

| Blocker | What it costs |
|---|---|
| **`basePath: '/agentdeck'`** in `next.config.mjs`, because Pages serves from a subdirectory | on an apex domain it must go, and every internal link changes with it |
| **`test_docs_site_links_in_repo_markdown_reach_a_real_page`** is hardcoded to `sagi5060.github.io` | needs updating in the same commit as the README |
| **Canonical URLs and redirects** (item 19) | GitHub Pages should redirect rather than leave two live copies competing for the same queries |

**Sequence:** DNS + CNAME → drop `basePath` → update the pin/link tests → set the repo homepage →
canonical tags → `sitemap.xml` and `robots.txt` → Search Console. Items 1, 17–20.

The Ask AgentDeck backend already answers on `ask.agentdecksdk.com`, so the subdomain half is proven
and its API URL is a build-time variable  -  the docs move does not disturb it.

## 4. Machine discovery  -  where this project has an unfair advantage

Items 9–16, worth doing early: coding agents are a real distribution channel and the supply of
well-structured Markdown is small. The docs are already anti-rot tested, the samples already execute
in CI and the reference pages are already generated, so an LLM-facing corpus built from them is
trustworthy in a way most projects' cannot be.

- **`llms.txt` / `llms-full.txt`**, generated from the docs build.
- **`context7.json`**, controlling exactly what Context7 indexes.
- **Clean Markdown alongside the rendered site**, so a crawler gets prose rather than a React shell.
- **First paragraphs that define the thing**: what AgentDeck is, what it solves, which frameworks it
  wraps, when to use it  -  the highest-leverage prose change, because it is what retrieval quotes.

## 5. Searchable content  -  the slow, compounding half

Items 21–25 and 30–33. Where the goal at the top is won or lost, and none of it is mechanical.

- **Problem-first guides** matter more than feature pages, because the target reader searches a
  *symptom*: "pause and resume an AI agent", "durable human approval", "resume a workflow from
  another process", "share runtime context across agents and tools".
- **Integration and wrapping guides** are the positioning made concrete  -  *"use your existing
  LangGraph agent with AgentDeck"* is the whole thesis in one page title.
- **Comparison pages** rank well and are read at decision time, and only work if they name what the
  alternative does better.
- **Articles, launch posts, forum answers** (30–33) move the numbers and no tooling produces them.
  Being findable is necessary and not sufficient.

## 6. Examples  -  five to ten serious ones

Items 26–29. Four exist: `chat-agent-with-a-tool`, `workflow-with-an-approval`,
`existing-langgraph-agent`, and `ask-agentdeck`  -  the last a real application. Every one is exercised
by the gate, which is item 28 already satisfied.

**Amended 2026-08-13.** This paragraph listed `agent-with-a-skill` as shipped; it never was, and its
absence is what #242 is about  -  skills are the one thing `Deck.from_project()` discovers with no
runnable example, so the SKILL.md contract gets learned from an error message. #242 is open as a
`good first issue`. `existing-langgraph-agent` shipped in its place and closes the existing-LangGraph
line.

Still missing: a customer-support agent, a long-running resumable workflow, a multi-agent system, an
MCP-connected agent, an agent with a skill, one agent served over both HTTP and chat.

## 7. Measurement

Items 38–40. Define ~30 target questions, run them monthly against Google, GitHub, ChatGPT Search,
Context7 and Perplexity, and record: are we returned, which page, what position, who appears instead.
Track it like a gate  -  a query that regresses is a defect with a cause. Registries and
machine-readable docs index in days; search position and community reference take months and depend
mostly on §5, which is writing.

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

The source plan's own priority order, with GitHub metadata pulled forward because it was free and
already done, and PyPI ahead of the domain because it blocks Context7 and needs no DNS.

## 9. The contributor loop

A different funnel from §5–§7: a contributor is acquired through GitHub issue search, not through
a query about a problem.

```
good first issue → repository → runs an example → contribution → merge → recognition → star / return
```

The step that leaks is **"runs an example"**  -  someone arriving from issue search has no idea what
AgentDeck is, and without a reason to run it they make a text edit and leave. Every newcomer issue
therefore carries a block naming *one specific example to run first*, chosen for proximity to that
issue's subject.

### The pool

Keep **5–10 open** at any time, each genuinely finishable in 30 minutes to 3 hours, and prefer tasks
that force the contributor to run the SDK over pure text maintenance. The findings backlog is the
natural source  -  a finding is already a scoped, reproduced defect with a proposed shape. Work that is
well-defined but too large for a first contribution gets `help wanted` instead. Do not manufacture
easy issues to fill the pool: an artificial issue is discovered as artificial during the work, which
is a worse first impression than an empty label.

### First merge

The moment a first contribution merges is the only point where recognition is both cheap and
believed. Standard reply:

> Thanks @username  -  merged. You're now an AgentDeck contributor.
>
> If you found AgentDeck useful, starring the repository helps the SDK reach more developers.
> Happy to have you pick up another `good first issue` or `help wanted` task  -  and if something
> was confusing while you worked on this, that is worth an issue of its own.

Never a condition, never asked before the merge.

### What CI enforces today, and what it does not

Audited 2026-08-13 against `.github/workflows/ci.yml`, `docs-check.yml` and
`PULL_REQUEST_TEMPLATE.md`.

| Expectation | Enforced? |
|---|---|
| lint · typecheck · import contracts · tests | **yes**  -  `ci.yml`, on every PR whatever its base |
| a subsystem silently skipping out of the gate | **yes**  -  the junit-xml skip-reason grep |
| goldens replay in a clean process | **yes**  -  a second `pytest tests/golden` run |
| docs-site builds and exports | **yes**  -  `docs-check.yml` |
| docs-site code samples still import what they claim | **yes**  -  `tests/test_docs_site.py` |
| internal and repo→docs-site links resolve | **yes**  -  same file |
| every public `Deck` method is documented | **yes**  -  same file |
| examples still build | **yes**  -  `tests/test_examples.py` |
| **CHANGELOG entry for a user-visible change** | **no**  -  checklist only |
| **the PR template's checkboxes** | **no**  -  nothing parses the PR body |
| **PR opened against `main` instead of `dev`** | **no**  -  and this is the likeliest newcomer mistake |

The first two stay honour-system by design: "user-visible" is a judgement call, and a bot
demanding a CHANGELOG line on a typo fix teaches contributors to tick boxes. The third is worth a
small check  -  mechanical, unambiguous, and it currently costs a newcomer a rebase.

One thing to know rather than fix: a fork PR from a first-time contributor needs a maintainer to
approve the workflow run before CI reports anything, and that silence looks like being ignored.
Approve the run before reviewing.
