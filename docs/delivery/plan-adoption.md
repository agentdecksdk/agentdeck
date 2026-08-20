# Plan: discoverability and adoption

**Status:** active · **Initial baseline:** 2026-08-12 at `d6a877d`, v3.0.0 released.

> Someone who **does not know AgentDeck exists**, but describes a problem it solves, gets
> **AgentDeck SDK** back as one of the relevant answers.

Not "someone who already knows the name can find it." That distinction rules out most
logo-and-tagline work and rules in package registries, machine-readable docs, and problem-shaped
pages.

## 0. What is already true

Updated 2026-08-20.

| | State |
|---|---|
| **Distribution and import** | `pip install agentdeck-sdk`, then `import agentdeck`. PyPI refused the preferred distribution name because it is too similar to the abandoned `agent-deck` placeholder |
| **`agentdeck-ai` on PyPI** | **taken, and active**  -  7 releases, last 2026-07-13, *"the game console for AI agents"*, unrelated domain |
| **`github.com/agentdeck`** | **held by that same project.** 0 public repos, created 2025-06-09 |
| **`agent-deck` on PyPI** | dead placeholder, *"Your Name"*, one release Aug 2025 |
| **GitHub description** | **done**  -  one sentence naming the product, the engines it wraps, and the runtime capabilities it supplies |
| **GitHub topics** | **done**  -  all twelve from the source plan |
| **Repo homepage URL** | `https://agentdecksdk.com/` |
| **Social preview image** | **set** from `docs/brand/social-card.svg` on 2026-08-12 |
| **Discussions** | **enabled** |
| **Wiki** | **disabled** |
| **PyPI publishing** | **shipped** through Trusted Publishing in `release.yml` |
| **Docs site** | Nextra static export on `https://agentdecksdk.com/`, deployed on release publish |
| **Generated docs** | `scripts/generate_docs_reference.py` already renders settings, CLI and changelog, gate-checked |

## 1. Rulings taken

| # | Ruling | Why |
|---|---|---|
| 1 | **One distribution: `agentdeck-sdk`; one import: `agentdeck`.** | PyPI refused `agentdeck` as too similar to the abandoned `agent-deck` placeholder. Every install surface must state the split until the open PEP 541 recovery request is resolved |
| 2 | **Brand is "AgentDeck SDK".** | The brand matches the distribution and differentiates the project from the unrelated `agentdeck-ai`; the import remains the concise `agentdeck` namespace |
| 3 | **Extensions are extras, not distributions.** | `agentdeck-sdk[serve]`, `[observability]`, and `[durability]` keep one release path. A separate distribution must earn a distinct release cadence, maintainer, or dependency boundary |
| 4 | **Register nothing speculatively.** | An unused PyPI name is not a durable defense. Publish only distributions the project actually maintains |
| 5 | **The namespace grant is the real mechanism, and it waits** | [PEP 752](https://peps.python.org/pep-0752/) (accepted 2026-06-29) reserves `agentdeck-*` so an impostor's upload fails with `409`; [PEP 755](https://peps.python.org/pep-0755/) (draft) makes it organization-only and requires *"actively using the namespace"*, which we do not until a real `agentdeck-*` exists. Its best value is not defensive  -  a grant can be **delegated**, which is how a community adapter gets blessed while an impostor gets refused |

## 2. Foundation shipped

The reversible infrastructure work is complete:

| # | Shipped | Evidence |
|---|---|---|
| 1 | **Publish `agentdeck-sdk` to PyPI** | `release.yml` uses Trusted Publishing and links deployments to the owned project |
| 2 | **Set a social preview image** | `docs/brand/social-card.svg` is the source for the uploaded GitHub preview |
| 3 | **Enable Discussions and disable Wiki** | Questions have one community surface rather than competing with an empty wiki |
| 4 | **Publish project pages** | Known Issues and the generated changelog are live; a current roadmap remains outstanding |
| 5 | **Generate `/llms.txt` and `/llms-full.txt`** | `scripts/generate_docs_reference.py` produces both; the gate byte-pins the index and proves the full corpus regenerates |

## 3. Current domain topology

`https://agentdecksdk.com/` is the canonical documentation origin. The same apex domain routes
`/ask` to Jack's backend, while the static site remains the fallback for every other path.

`DOCS_SITE_URL` and `JACK_API_URL` are build-time repository variables. Both point at the apex;
the browser appends `/ask` for Jack. Canonical metadata, `sitemap.xml`, and `robots.txt` use the
same origin.

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

Items 26–29. Five exist: `agent-with-a-skill`, `chat-agent-with-a-tool`,
`existing-langgraph-agent`, `jack`, and `workflow-with-an-approval`. Every one is exercised by the
gate, which is item 28 already satisfied.

Still missing: a customer-support agent, a long-running resumable workflow, a multi-agent system,
an MCP-connected agent, and one agent served over both HTTP and chat.

## 7. Measurement

Items 38–40. Define ~30 target questions, run them monthly against Google, GitHub, ChatGPT Search,
Context7 and Perplexity, and record: are we returned, which page, what position, who appears instead.
Track it like a gate  -  a query that regresses is a defect with a cause. Registries and
machine-readable docs index in days; search position and community reference take months and depend
mostly on §5, which is writing.

## 8. Sequence

```
SHIPPED  PyPI · domain · Context7 · llms.txt · social preview · Discussions
NEXT     problem-first guides · integration pages · comparisons · /why-agentdeck
THEN     serious examples · articles · launch posts · forum presence
ONGOING  monthly discoverability measurement
```

The infrastructure phase is complete. Searchable writing and credible examples now determine
whether the shipped discovery surfaces have useful material to return.

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
