# 11. Branding, polish, and adoption

The brand system here is unusually good for a three-week-old project: a component-based SVG kit with a named palette, a first-contribution bot that posts branded cards, a measured discoverability baseline, and a live agent on the landing page that is built with the product. The adoption machinery is real and mostly shipped. What is missing is the writing: the plan named the one page that could win the one query nobody serves, and that page is seven lines.

Judged as brand surface, not docs correctness (report 06 covers the latter).

---

## GOOD

### The mark is a design system, not a logo file [GOOD] (severity: medium)
Eleven vectors composed from three parts drawn in one shared coordinate space, so a composition pastes a `<path>` with no transform. Every variant exists for a stated reason and was measured in a browser.

```
| `components/card.svg` | the ace-cut card carrying the A, which is a hole in the path |
| `components/spark.svg` | the spark alone, drawn geometry, box `880 152.5 196.8 196.7` |
| `components/wordmark.svg` | `agentdeck` as nine outlines from Poppins-SemiBold |

**Every part is drawn in the mark space `246 145 832 933`, so a composition pastes the `<path>`
with no transform at all.**
```
Evidence: `docs/brand/README.md:45-56`

### Vectors only, with the rule enforced by .gitignore [GOOD] (severity: low)
Rasters are treated as build artifacts with a documented regeneration recipe, so the brand directory stays reviewable in a diff. Only two PNGs exist in the tree, and they are the two a GitHub comment genuinely needs.

```
Rasters that a GitHub comment or the social-preview upload genuinely needs are the one exception,
and they belong under `.github/assets/`, not here. Everything in this directory regenerates from
a recipe, so treat a PNG next to these files as a build artifact that escaped.
```
Evidence: `docs/brand/README.md:124-126`; `find . -name '*.png'` returns only `.github/assets/contributors/{merged,welcome}.png`

### The headline is one sentence and it is the same sentence everywhere it is a headline [GOOD] (severity: medium)
Four independent surfaces carry the identical hero line and subtitle, including the JSON-LD a crawler reads. No drift on the thing a reader sees first.

```
README.md:7:        **Agentic software should feel like software.**
hero.tsx:222:       <h1>Agentic software should feel like software.</h1>
index.mdx:2:        title: Agentic software should feel like software
layout.tsx:85:      + 'one execution model you can observe, control and extend.'
```
Evidence: `README.md:7`, `docs-site/app/hero.tsx:222`, `docs-site/content/index.mdx:2`, `docs-site/app/layout.tsx:84-85`

### The install/import split was forced, and it is mitigated on every surface [GOOD] (severity: medium)
`pip install agentdeck-sdk` then `import agentdeck` is the classic papercut, and it was not chosen. PyPI refused `agentdeck` as too similar to a squatted placeholder. Every surface that shows the install line also states the split, and a PEP 541 recovery path is open.

```
- **The distribution is renamed `agentdeck` -> `agentdeck-sdk`.** Not a preference: PyPI refuses
  the name `agentdeck` as *"too similar to an existing project"*  -  an abandoned `agent-deck`
  placeholder (one release, author `"Your Name"`, summary *"A placeholder package"*).
  A PEP 541 request for the squatted name is open. If it is granted, `agentdeck` becomes the
  distribution and `agentdeck-sdk` becomes a shim that depends on it.
```
Evidence: `CHANGELOG.md:497-510`; mirrored at `README.md:210`, `docs-site/app/install-line.tsx:5-7`, `context7.json:25`
Ref: https://peps.python.org/pep-0541/

### The discoverability baseline is real measurement, not a vanity dashboard [GOOD] (severity: high)
Thirty verbatim questions, two channels scripted, positional metrics only, and a stated refusal to conflate "not measured" with "not returned". It reports zero across the board and calls that the correct reading.

```
| **Context7** | **0 / 30** | all 30 questions, scripted against the search API |
| **GitHub repository search** | **1 / 30** | all 30 questions, `search/repositories` top 10 |
| **Web search** | **0 / 8** | the 8 highest-value questions |

**The one GitHub hit is not a real one.** `one ordered event log per agent run` ranks **#1**
because that phrase is lifted verbatim from the repository description.
```
Evidence: `docs/delivery/discoverability-baseline.md:17-26`

### The baseline found an unserved query and named the competitors nobody had found [GOOD] (severity: high)
"Combine the OpenAI Agents SDK with LangGraph" returns only versus articles. Two near-competitors that wrap rather than replace were identified, with the honest note that a comparison page ignoring them gets discounted.

```
**1. "Combine the OpenAI Agents SDK with LangGraph" is an unserved query.** Two independent
searches reported the gap in their own words. Every result is a *versus* article. That open lane
makes `use-your-existing-langgraph-agent` the highest-value page to write, ahead of
`/why-agentdeck`.
```
Evidence: `docs/delivery/discoverability-baseline.md:49-57`

### The first-contribution bot is idempotent, least-privilege by intent, and branded [GOOD] (severity: high)
`pull_request_target` with a documented safety contract (no checkout, no PR code, PR-controlled text passed through `env:` not `${{ }}`), HTML-comment markers to prevent duplicates, and a pipe-buffering bug pre-empted in a comment.

```
# Capture before grepping. Piped straight into `grep -q`, the early exit closes the
# pipe, gh takes SIGPIPE, and `pipefail` turns that into a false negative - which posts
# a second copy of the thing the marker exists to prevent.
marker='<!-- agentdeck:first-pr-welcome -->'
bodies=$(gh api "repos/$REPO/issues/$NUMBER/comments" --paginate --jq '.[].body')
```
Evidence: `.github/workflows/first-contribution.yml:50-58`, safety contract at `:3-11`

### The star ask is post-merge only, never a condition [GOOD] (severity: medium)
The recognition job fires on `closed && merged == true`, and the plan states the rule explicitly. This is the correct discipline and most projects get it wrong.

```
# Recognition at the one moment it is both cheap and believed. Fires *after* the merge, so the
# star is never a condition of it.
thanks:
  if: >
    github.event.action == 'closed' &&
    github.event.pull_request.merged == true &&
```
Evidence: `.github/workflows/first-contribution.yml:89-94`; ruling at `docs/delivery/plan-adoption.md:173`

### The in-docs Contribute block is a conversion surface at the point of frustration [GOOD] (severity: medium)
A typed component that invites exactly one issue, states what is missing in prose written for that page, and labels difficulty and scope. Its own docstring refuses the lazy version.

```
interface ContributeProps {
  /** The one issue this invites. A label listing is not a first contribution. */
  issue: number
  difficulty: Difficulty
  scope: Scope
  /** What is missing, in one sentence, written for this page. */
  need: string
```
Evidence: `docs-site/app/contribute.tsx:8-16`

### llms.txt is generated from the docs and byte-pinned in the gate [GOOD] (severity: high)
The LLM-facing index cannot drift from the site, because a test compares it byte for byte against its generator. Most projects hand-write this file and it rots in a week.

```
def test_llms_txt_matches_the_generator() -> None:
    assert LLMS_PAGE.read_text() == render_llms_txt(), f"{LLMS_PAGE} is stale  -  {_REGEN_HINT}"
```
Evidence: `tests/test_generated_reference.py:51-52`; generator at `scripts/generate_docs_reference.py:343-409`

### context7.json scopes the index and encodes the ten things an LLM gets wrong [GOOD] (severity: high)
Folder scoping, the changelog excluded, and ten behavioural rules covering the actual footguns: the `Context[T]` decorator trap, the interrupt re-run rule, the unknown-event-kind default case, and a pointer at the Known Issues page.

```
"A tool is a plain Python function passed to `Agent(tools=[...])`; `build()` compiles it. A tool
 declaring an `agentdeck.Context[T]` parameter must NOT use `@function_tool`.",
"Known defects are listed at /known-issues and should be honoured when advising - notably that a
 tool which raises still completes the run."
```
Evidence: `context7.json:7-33`

### Jack is the strongest marketing asset in the repo, and he is live on the landing page [GOOD] (severity: high)
An agent built entirely with the product, answering questions about the product, on the product's homepage, with his own event tree rendered beside him. The README is his source code.

```
Jack is an AgentDeck developer agent running on this site. Ask him about the SDK,
architecture, integration, or paste code you are working with. The tree beside him is
his actual run: every node is an event the runtime emitted, in the order it emitted it.
```
Evidence: `docs-site/app/landing-components.tsx:137-140`; README opens with "Everything below is his actual source" at `README.md:29-31`

### Jack is quality-gated: 50 goldens and 40 offline tests [GOOD] (severity: medium)
The live demo cannot silently regress into inventing an API, because grounding is an exact token check against the corpus and the checks run in `make check`.

```
**Baseline:** 40 offline tests in the gate, 50 goldens in `examples/jack/evalset.py`,
one custom runner.
| Did he invent an API? | **exact string match**, keep | A token in no page is a fact about two
  strings. No judge beats `in`, and a judge costs money to be worse. |
```
Evidence: `docs/delivery/plan-jack-eval.md:3-4,32`

### Release notes are the curated CHANGELOG section, with no auto-generated fallback [GOOD] (severity: medium)
A release without hand-written notes fails the workflow rather than shipping a commit list. Fifteen releases, every one with real notes.

```
# A missing section fails loudly  -  a release without curated notes is the bug this
# step exists to prevent, so there is no --generate-notes fallback.
if [ ! -s /tmp/release-notes.md ]; then
  echo "No CHANGELOG section found for heading '## [$version]'" >&2
```
Evidence: `.github/workflows/release.yml:92-104`

### Known Issues is published as a trust signal and reads like one [GOOD] (severity: medium)
Eight reproduced defects, grouped by failure mode, leading with the worst one in a callout, and stating why the page exists. A project that publishes its silent-failure list is telling an evaluator it can be trusted with the ones it has not found.

```
Everything here is real, reproduced, and open against **v4.0.0**. It is published rather than
quietly tracked because most of these fail *silently* - you get a plausible wrong answer, not an
error - and an hour spent debugging one of them is an hour this page could have saved.
```
Evidence: `docs-site/content/resources/known-issues.mdx:7-9`

### Repo topics and description were set deliberately against a query list [GOOD] (severity: medium)
Twelve topics covering the searchable concepts, not the technology stack. The plan tracked this as a shipped item and it is.

```
$ gh api repos/agentdecksdk/agentdeck --jq .topics
["agent-framework","agent-observability","agent-runtime","agent-workflows","agentic-ai",
 "ai-agents","human-in-the-loop","langgraph","mcp","multi-agent","openai-agents","python"]
```
Evidence: `gh api repos/agentdecksdk/agentdeck --jq '.topics'`; plan row at `docs/delivery/plan-adoption.md:23`

### The clean-room outsider review is rare discipline, and it fed the issue tracker [GOOD] (severity: high)
Four reviewers given only the wheel, the README, the docs site and `examples/`. No repo access. Round two added a mandatory adversarial phase and a rule that "the docs don't cover X" needs a pasted grep. Its output became the `finding:`-labelled issues.

```
| C | sonnet | agent surface + adversarial | key never arrived; **stubbed the model endpoint**
      and reproduced 6 findings anyway |
| D | sonnet | ops surface + adversarial | ran end to end, **7 defects, 1 of them P0** |

**The model mattered less than the adversarial phase** - B and D built the same kind of workflow,
and only the one told to break it found anything.
```
Evidence: `docs/delivery/review-v3-outsider.md:11-19`

### CONTRIBUTING.md opens by telling the reader to run the product [GOOD] (severity: medium)
The plan identified "runs an example" as the leaking step in the contributor funnel, and the file acts on it in its first section, before the branch model or the setup instructions.

```
Before picking up an issue, run one of the projects in [`examples/`](examples/) as a user would
(`pip install agentdeck-sdk`, then `python run.py`). Fifteen minutes there makes the rest of this
file, and most issues, read very differently.
```
Evidence: `CONTRIBUTING.md:9-11`; the diagnosis at `docs/delivery/plan-adoption.md:148-151`

### LICENSE, SECURITY.md, CODE_OF_CONDUCT.md and PR/issue templates all present and real [GOOD] (severity: low)
MIT, a Contributor Covenant with a working reporting address, four typed issue forms, and a SECURITY.md that names what is deliberately undefended. The boxes an evaluator checks are checked.

```
LICENSE                  MIT, Copyright (c) 2026 Sagi Shabtai
CODE_OF_CONDUCT.md:39    reported to the community leaders ... at sagi.shabtai@outlook.com
.github/ISSUE_TEMPLATE/  bug_report.yml docs_contribution.yml feature_request.yml finding.yml
```
Evidence: `LICENSE:1-3`, `CODE_OF_CONDUCT.md:39`, `ls .github/ISSUE_TEMPLATE/`

---

## BAD

### context7.json tells an LLM there is no agentdeck-sdk package, one line above the pip install line [BAD] (severity: high)
Rule 24 was written when the distribution was `agentdeck` and never updated when rule 25 was added. The result is a direct contradiction in the highest-priority field of the channel the adoption plan bets on, on the single most consequential sentence: how to install.

```
"rules": [
  "The import name is `agentdeck`. The project is branded AgentDeck SDK; there is no
   `agentdeck-sdk` package, and `agentdeck-ai` on PyPI is an unrelated project.",
  "Install and import names differ: `pip install agentdeck-sdk`, then `import agentdeck`.
   Never write `pip install agentdeck` - that name is taken by an unrelated project.",
```
Evidence: `context7.json:24-25`

### The page the baseline named as the one winnable query is seven lines [BAD] (severity: high)
The measurement doc identified `use your existing LangGraph agent` as the highest-value page to write, ahead of `/why-agentdeck`, because nothing on the internet answers it. All four integration pages are stubs. The differentiation is measured, prioritised, and unwritten.

```
# Existing Agents

Wrap existing agents without requiring a rewrite.

## Wrapping Agents

Bring your custom agent implementations and wrap them with AgentDeck's runtime.
```
Evidence: `docs-site/content/integrations/existing-agents.mdx` (7 lines, entire file); priority stated at `docs/delivery/discoverability-baseline.md:56-57`

### The four stub pages enter llms.txt as bare lowercase slugs with no description [BAD] (severity: high)
The index an LLM reads to choose which page to fetch describes every generated reference page and describes none of the four positioning pages, because stubs carry no frontmatter. In the retrieval channel, the differentiation is invisible.

```
- [CLI](https://agentdecksdk.com/reference/cli): The agentdeck command tree, generated from ...
- [Deck](https://agentdecksdk.com/reference/deck): The composition root ...
- [existing-agents](https://agentdecksdk.com/integrations/existing-agents)
- [langgraph](https://agentdecksdk.com/integrations/langgraph)
- [mcp](https://agentdecksdk.com/integrations/mcp)
- [openai-agents-sdk](https://agentdecksdk.com/integrations/openai-agents-sdk)
```
Evidence: `docs-site/public/llms.txt:22-38`

### The site declares a large-image social card and supplies no image [BAD] (severity: high)
`summary_large_image` with no `openGraph.images` renders a blank or degraded card on every share to X, LinkedIn, Slack and Discord. `social-card.svg` sits in `docs-site/public/brand/` and is referenced by nothing.

```
  openGraph: {
    type: 'website',
    siteName: 'AgentDeck SDK',
    url: './',
    title: 'AgentDeck SDK  -  a production runtime for AI agents',
    description: '...'
  },
  twitter: { card: 'summary_large_image', title: 'AgentDeck SDK' },
```
Evidence: `docs-site/app/layout.tsx:26-35`; unused asset at `docs-site/public/brand/social-card.svg`
Ref: https://developer.x.com/en/docs/twitter-for-websites/cards/overview/summary-card-with-large-image

### The greeting workflow was dead for four days and every run reported green [BAD] (severity: high)
A least-privilege tightening on 2026-08-15 dropped both jobs to `pull-requests: read`, which `gh pr comment` cannot use. No run went red, because a maintainer's own PR takes the early-exit path and exits 0. It surfaced only when a human noticed a missing comment on PR #359.

```
fix(ci): first-contribution jobs need pull-requests: write to comment (#360)

gh pr comment posts via the addComment GraphQL mutation, which needs
pull-requests: write. Both jobs only granted pull-requests: read, so
every welcome/thanks comment failed with "Resource not accessible by
integration" (surfaced on PR #359).
```
Evidence: `git show f1f650c`; the tightening at `git show 6bbe9ca:.github/workflows/first-contribution.yml` lines 28, 99

### Neither real outside contributor ever received the automated welcome [BAD] (severity: high)
Two external PRs exist. #276 opened one day before the workflow was written; #359 opened while it was broken, and the maintainer pasted the bot's body by hand, marker included. The conversion surface has a 0-for-2 record on the half that matters, the one that fires while the contributor is still waiting.

```
$ gh pr view 359 --repo agentdecksdk/agentdeck --json comments
@sagi5060: <!-- agentdeck:first-pr-welcome -->  Thanks for opening your first PR here, @xjc...
@github-actions: <!-- agentdeck:first-merged-pr -->  <p align="center">   <img src="https://raw.g
```
Evidence: `gh pr view 359 --repo agentdecksdk/agentdeck --json comments`; PR #276 createdAt `2026-08-12T23:01:03Z` against workflow first commit `86880c6` (2026-08-13)

### The GitHub repository description is visibly broken [BAD] (severity: medium)
An em-dash removal pass stripped the separator and left the double space behind, in the one field that appears in GitHub search results, the org page, and every social unfurl. It also concatenates three sentences where the plan calls for one.

```
$ gh api repos/agentdecksdk/agentdeck --jq .description
AgentDeck SDK  Agentic software should feel like software. Build agents, tools and
workflows as normal software. AgentDeck gives them one execution model you can
observe, control and extend.
```
Evidence: `gh api repos/agentdecksdk/agentdeck --jq '.description'`

### The one-sentence description diverges on eight secondary surfaces [BAD] (severity: medium)
The headline holds, the description does not. Six distinct nouns for the product across the surfaces a reader actually hits: harness, runtime harness, production runtime, production runtime and harness, declarative harness, Python harness. PyPI, the highest-traffic discovery surface for a Python library, gets a list of internals.

```
pyproject.toml:4   Declarative harness over the OpenAI Agents SDK and LangGraph: settings,
                   capabilities, skills, runners, graph compilation, plug-in discovery.
layout.tsx:60      AgentDeck SDK - Compose. Observe. Ship.
layout.tsx:91      A declarative runtime harness for multi-agent systems, wrapping ...
overview.mdx:8     a production runtime and harness for AI agents and multi-agent workflows
CONTRIBUTING.md:5  a production runtime around agents you already have
llms.txt:3         The production runtime for agents you already have
context7.json:6    A Python harness for agents you have to operate
```
Evidence: `pyproject.toml:4`, `docs-site/app/layout.tsx:60`, `docs-site/app/layout.tsx:91`, `docs-site/content/meet-agentdeck/overview.mdx:8`, `CONTRIBUTING.md:5`, `docs-site/public/llms.txt:3`, `context7.json:6`

### The best positioning prose in the repo is in two files no prospect reads [BAD] (severity: medium)
`llms.txt` states what it is, what it wraps, and when to use it, in three paragraphs. CONTRIBUTING.md has the crispest one-sentence differentiation anywhere. Neither is on the landing page, which opens with an abstraction.

```
It wraps the OpenAI Agents SDK and LangGraph rather than replacing them: an `Agent` compiles to
an SDK agent, a `Workflow` compiles to a LangGraph graph, and each is executed by its own engine.
AgentDeck owns configuration; the engines own execution. There is no agent loop here and no graph
engine of its own.

Use it when the wiring around an agent has become the work.
```
Evidence: `docs-site/public/llms.txt:10-17`; the CONTRIBUTING variant at `CONTRIBUTING.md:5-7`

### Nothing answers "why not just LangGraph" [BAD] (severity: high)
No comparison page, no `/why-agentdeck`, no benchmarks. The plan calls comparison pages high-value and read at decision time; the baseline named the two near-competitors a credible one must address. Thirty-three pages, zero of them decision-stage. The closest thing shipped is a "what it deliberately does not do" list in the README, which is honesty rather than differentiation.

```
$ find docs-site/content -name '*.mdx' | wc -l
33
$ ls docs-site/content/resources/
changelog.mdx  known-issues.mdx  migration-guides.mdx  troubleshooting.mdx
```
Evidence: `find docs-site/content -name '*.mdx'`; plan row at `docs/delivery/plan-adoption.md:93-94`

### "Production runtime" is claimed on five surfaces against a Beta classifier and four majors in twenty days [BAD] (severity: high)
An evaluator who reads the pitch and then the release list sees a contradiction. v1 through v4 shipped between 2026-07-26 and 2026-08-16, five v4 patches in four days. The README does say beta, at line 235, far below the install line.

```
$ gh release list --repo agentdecksdk/agentdeck --limit 6
v4.0.5  2026-08-19    v4.0.1  2026-08-18
v4.0.4  2026-08-19    v4.0.0  2026-08-16
v4.0.3  2026-08-19    v3.1.0  2026-08-12
pyproject.toml:12   "Development Status :: 4 - Beta",
```
Evidence: `gh release list --repo agentdecksdk/agentdeck`; `pyproject.toml:12`; beta admission at `README.md:235`

### The deck-and-cards metaphor exists only in pixels [BAD] (severity: low)
The mark is an ace-cut card, the hero animates module cards converging into a Deck, the bot posts contributor cards, and the palette is named Agent Blue and Ace Red. The product language never uses it: no deck of agents, no hand, no shuffle, no card anywhere in the prose. A naming asset that carries the whole visual system and earns nothing in the copy.

```
$ grep -riE 'deck of |cards|shuffle|hand of' README.md docs-site/content/
README.md:34: An agent is a declaration. A Deck is where an agentic application comes together.
(no other match: `Deck` is a class name and `deck` a lowercase synonym for it)
```
Evidence: `grep -riE 'deck of |cards|shuffle' README.md docs-site/content/`; the metaphor at `docs/brand/README.md:3-5`

### The copyable pip line is at the bottom of the landing page [BAD] (severity: medium)
`InstallLine` exists, is well built, and is used once, in `FinalCTA`. The hero offers "Get started" and "GitHub". On a Python library landing page the copyable install command is the highest-converting element above the fold.

```
$ grep -rn 'InstallLine' docs-site/
docs-site/app/install-line.tsx:9:export function InstallLine() {
docs-site/app/landing-components.tsx:162:      <InstallLine />
```
Evidence: `docs-site/app/landing-components.tsx:158-162`; hero actions at `docs-site/app/hero.tsx:229-237`

### The public roadmap shipped and was silently deleted [BAD] (severity: medium)
`roadmap.mdx` landed in PR #257 as one of the three project pages, then vanished in the docs restructure. `context7.json` still excludes the ghost. "Is this maintained and where is it going" now has no published answer, and the plan called that page half the reason Known Issues was written.

```
$ git log --oneline --diff-filter=D -- 'docs-site/content/roadmap.mdx'
4134f1d docs: the em dash is gone, the docs are restructured, the landing pag...

context7.json:19  "excludeFiles": [
context7.json:21    "roadmap.mdx"
```
Evidence: `git log --diff-filter=D -- docs-site/content/roadmap.mdx`; `context7.json:21`

### Five of eleven good first issues omit the "run this example first" block the plan mandates [BAD] (severity: medium)
The plan diagnoses "runs an example" as the leaking funnel step and says every newcomer issue therefore carries the block. The six finding-derived issues have it. The five newest, all opened after the plan, do not.

```
$ for n in 353 352 351 335 334 333 332 254 246 241 235; do ... done
353 block=0   352 block=0   351 block=0   335 block=0   334 block=0
333 block=1   332 block=1   254 block=1   246 block=1   241 block=1   235 block=1
```
Evidence: `gh issue view <n> --repo agentdecksdk/agentdeck --json body`, grepped for "run this example|as a user would"; the rule at `docs/delivery/plan-adoption.md:148-151`

### Four good first issues are a high-severity architectural gap in disguise [BAD] (severity: medium)
Issues #332 to #335 ask a stranger to add Redis and Postgres `ControlPort` and `LeasePort` implementations. Report 04 records the same absence as high severity: no control or lease backend exists for a multi-machine deployment. Either the label is wrong or the gap is not owned, and a newcomer who takes one discovers which.

```
335  2026-08-16  [good first issue]  leases: add Postgres LeasePort support
334  2026-08-16  [good first issue]  control: add Postgres ControlPort support
333  2026-08-16  [good first issue]  leases: add Redis LeasePort support
332  2026-08-16  [good first issue]  control: add Redis ControlPort support
```
Evidence: `gh issue list --repo agentdecksdk/agentdeck --label "good first issue"`; cross-reference `reports/04-adapters.md`, "Control and lease ports stop at memory and sqlite [BAD] (severity: high)"

### Discussions is five self-seeded threads with zero replies, and the announcement trail stopped at v3.1.0 [BAD] (severity: medium)
All five posted by the maintainer on one day, one upvote each, no comment a week later. Four releases have shipped since the last announcement, including a major. The corpus argument for enabling Discussions holds; the community signal is negative.

```
2026-08-13 @sagi5060 comments=0 up=1 :: Where should the line between runtime and framework sit?
2026-08-13 @sagi5060 comments=0 up=1 :: Which engine should AgentDeck support next?
2026-08-13 @sagi5060 comments=0 up=1 :: What are you building with AgentDeck?
2026-08-13 @sagi5060 comments=0 up=1 :: AgentDeck SDK v3.1.0 - `pip install agentdeck-sdk`
```
Evidence: `gh api graphql` on `repository.discussions`, fields `createdAt author comments.totalCount upvoteCount`

### There is no social proof to show, and the one proof widget shows a bot [BAD] (severity: medium)
Two stars, three forks, zero watchers. The README's contributor image resolves to five avatars: the maintainer, dependabot, two drive-by docs contributors, and `Claude <noreply@anthropic.com>`, which is an AI vendor signature the repo's own instructions forbid. No testimonials, no users, no download counts anywhere.

```
$ gh api repos/agentdecksdk/agentdeck/contributors --jq '.[] | "\(.login) \(.contributions)"'
sagi5060 279 · dependabot[bot] 6 · claude 1 · 1cbyc 1 · xjcway123 1

$ gh api 'repos/agentdecksdk/agentdeck/commits?author=claude'
60b95b61 Claude <noreply@anthropic.com> docs: add the 1.2.1 CHANGELOG entry
```
Evidence: `gh api repos/agentdecksdk/agentdeck/contributors`; README widget at `README.md:247-251`; the rule at `CLAUDE.md` section 5, "No attribution trailers"

### No launch trail exists at all [BAD] (severity: medium)
No blog, no `/blog` route, no announcement posts, no Show HN, no Reddit or forum trail, no newsletter, no conference or podcast mention. The plan lists articles and launch posts as the half that moves the numbers and says no tooling produces them; nothing has been produced.

```
docs/delivery/plan-adoption.md:95
- **Articles, launch posts, forum answers** (30-33) move the numbers and no tooling produces them.
  Being findable is necessary and not sufficient.
```
Evidence: `grep -rli 'blog|announcement|launch post|show hn' docs-site/content/ README.md` returns nothing outside `docs/delivery/`

### plan-adoption.md is still marked "proposed" and its headline ruling was reversed without amendment [BAD] (severity: medium)
Ruling 1 says "One package: `agentdeck`. Not `agentdeck-sdk`". The distribution is `agentdeck-sdk`. Section 6 carries a dated "Amended 2026-08-13" note for a much smaller correction, so the convention exists and was not applied to the ruling that matters.

```
**Status:** proposed · **Date:** 2026-08-12
| 1 | **One package: `agentdeck`.** Not `agentdeck-sdk`, and no meta-package | ...
```
Evidence: `docs/delivery/plan-adoption.md:3,36`; the amendment convention at `:104`; the reversal at `CHANGELOG.md:497`

### The plan documents infrastructure that no longer exists [BAD] (severity: low)
Section 3 states the Ask AgentDeck backend answers on `ask.agentdecksdk.com` and calls the subdomain half proven. Both repo variables now point at the apex domain, so the subdomain is gone and the plan's blocker analysis reads against a topology that changed.

```
docs/delivery/plan-adoption.md:68
The Ask AgentDeck backend already answers on `ask.agentdecksdk.com`, so the subdomain half is
proven and its API URL is a build-time variable.

$ gh api repos/agentdecksdk/agentdeck/actions/variables
DOCS_SITE_URL=https://agentdecksdk.com · JACK_API_URL=https://agentdecksdk.com
```
Evidence: `docs/delivery/plan-adoption.md:68`; `gh api repos/agentdecksdk/agentdeck/actions/variables`

### The brand README says the social card is both delivered and still owed [BAD] (severity: low)
Two sections, one file, opposite claims. A reader who checks the "Still owed" list concludes GitHub renders its grey default; the section forty lines above says it was set on 2026-08-12 and gives the regeneration recipe.

```
:158  - **A 1280x640 social card.** GitHub renders its grey default on every share to X,
        LinkedIn, Slack and Hacker News until one exists

:162  `social-card.svg` is the 1280x640 image GitHub shows under every link to this
        repository in X, LinkedIn, Slack, Discord and Hacker News. Left unset - as it was
        until 2026-08-12 - GitHub renders its grey default
```
Evidence: `docs/brand/README.md:158` against `:162-164`

### The release workflow's PyPI environment URL points at the wrong project [BAD] (severity: low)
Every GitHub deployment record for every release since 3.1.0 links to `pypi.org/project/agentdeck/`, which is the squatted-adjacent name this project does not own. One character, on a link an evaluator clicks from the release page.

```
    environment:
      name: pypi
      url: https://pypi.org/project/agentdeck/
```
Evidence: `.github/workflows/release.yml:142-144`

### Nothing guards a Contribute block against pointing at a closed issue [BAD] (severity: low)
The mechanism depends on a human deleting the block when the issue merges, and PR #359's contributor had to do exactly that. `tests/test_docs_site.py` checks internal links, external hostnames and Deck method coverage; it does not check that `<Contribute issue={N}>` names an open issue.

```
$ grep -rn 'Contribute' tests/test_docs_site.py
(no output)

$ gh issue view 350 --json state
350 CLOSED docs: diagram the path from a Jack question to the browser
```
Evidence: `grep -rn 'Contribute' tests/test_docs_site.py` returns nothing; three live blocks at `docs-site/content/{jack/notes,meet-agentdeck/quickstart,resources/known-issues}.mdx`

### Known Issues pins v4.0.0 while v4.0.5 is current [BAD] (severity: low)
The page's credibility rests on being current, and its first line names a release five patches old. Cheap to fix, and the fix belongs in the release checklist rather than in a sweep.

```
Everything here is real, reproduced, and open against **v4.0.0**.
```
Evidence: `docs-site/content/resources/known-issues.mdx:7`; current release `v4.0.5` per `gh release list`

### The issue chooser has no config.yml, so blank issues are allowed and nothing links to Discussions [BAD] (severity: low)
Four good typed forms, and no `config.yml` to disable the blank-issue escape hatch or to add contact links pointing at Discussions and the docs. The chooser is the first screen a stranger with a question sees.

```
$ ls -a .github/ISSUE_TEMPLATE/
bug_report.yml  docs_contribution.yml  feature_request.yml  finding.yml
```
Evidence: `ls -a .github/ISSUE_TEMPLATE/`
Ref: https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository

### Five of the plan's ten serious examples exist, and the missing ones are the demo-worthy ones [BAD] (severity: low)
`agent-with-a-skill` closed one line of the missing list. Still absent: a customer-support agent, a long-running resumable workflow, a multi-agent system, an MCP-connected agent, and one agent served over both HTTP and chat. Those five are the ones a prospect screenshots.

```
$ ls examples/
agent-with-a-skill  chat-agent-with-a-tool  existing-langgraph-agent  jack
workflow-with-an-approval
```
Evidence: `ls examples/`; the target and the gap at `docs/delivery/plan-adoption.md:98-111`

### The landing page's live agent has no metric for whether its answers are useful [BAD] (severity: medium)
Grounding and citations are checked by exact match; usefulness is not measured, and an unhelpful-refusal bug already shipped. The demo is on the homepage, so a refusal is a brand event rather than a test failure.

```
| The answer is *useful*                | **no** | nothing. This is where the refusal bug lives |
| He stays in role under attack         | **no** | one manual stress test, not repeatable |
| Citations are real                    | **no** | nothing |
**Status:** plan. Nothing here is built.
```
Evidence: `docs/delivery/plan-jack-eval.md:6,20-22`

---

## Plan vs reality

| Planned item | Source | Status |
|---|---|---|
| Publish to PyPI | plan-adoption.md:48 | **shipped** (`PYPI_PUBLISH=true`, CHANGELOG 3.1.0) |
| One package named `agentdeck`, not `agentdeck-sdk` | plan-adoption.md:36 | **reversed**, forced by PyPI; plan never amended |
| GitHub description and topics | plan-adoption.md:22-23 | **shipped**, description text now mangled |
| Repo social preview image | plan-adoption.md:49 | **shipped** repo-side (2026-08-12) |
| Docs-site og:image | plan-adoption.md §4 | **not started**; `summary_large_image` with no image |
| Enable Discussions, disable Wiki | plan-adoption.md:50 | **shipped**; zero engagement, announcements stopped at v3.1.0 |
| Project pages: roadmap, known issues, changelog (#257) | plan-adoption.md:51 | **partial**; roadmap shipped then deleted in 4134f1d |
| `llms.txt` / `llms-full.txt` generated into the build | plan-adoption.md:52 | **shipped**, generated and byte-pinned in the gate |
| `context7.json` scoping the index | plan-adoption.md:79 | **shipped**, with the rule-24 contradiction |
| Domain cutover, canonical URLs, sitemap, robots | plan-adoption.md §3 | **shipped** (`site.ts`, `sitemap.ts`, `robots.ts`) |
| Clean Markdown alongside the rendered site | plan-adoption.md:80 | **shipped** via `llms-full.txt` |
| First paragraphs that define the thing | plan-adoption.md:81-82 | **partial**; written in `llms.txt`, not on the landing page |
| Problem-first guides | plan-adoption.md:88-90 | **not started** |
| Integration / wrapping guides | plan-adoption.md:91-92 | **not started**; four 7-line stubs |
| Comparison pages | plan-adoption.md:93-94 | **not started** |
| `/why-agentdeck` | plan-adoption.md:129 | **not started** |
| Articles, launch posts, forum presence | plan-adoption.md:95-96 | **not started** |
| Five to ten serious examples | plan-adoption.md:98-111 | **partial**, 5 of 10; the demo-worthy five missing |
| Monthly discoverability measurement | plan-adoption.md:115-119 | **shipped**; baseline taken, next reading 2026-09-12, not yet due |
| 5-10 good first issues open | plan-adoption.md:155 | **shipped**, 11 open |
| `help wanted` for oversized work | plan-adoption.md:158 | **shipped**, 3 issues |
| Every newcomer issue names one example to run | plan-adoption.md:148-151 | **partial**, 6 of 11 |
| Post-merge recognition, star never a condition | plan-adoption.md:162-173 | **shipped** in `first-contribution.yml` |
| First-PR greeting within a day | plan-adoption.md:19-20 (workflow) | **broken 08-15 to 08-19**; never auto-fired for either outside contributor |
| A check for PRs opened against `main` | plan-adoption.md:192-196 | **not started** |
| Jack evaluation beyond grounding | plan-jack-eval.md:6 | **not started**, plan only |

---

## Bottom line

The infrastructure half of the adoption plan is done and done well: PyPI, the domain, the LLM corpus, the contributor bot, a real measurement baseline, and a brand system that is a system rather than a logo. The writing half is untouched, and it is the half the project's own baseline document says decides the outcome, including the one page it identified as winnable. Three things would move more than anything else on this list: fix `context7.json` rule 24, wire an og:image, and write `use your existing LangGraph agent` as a real page.
