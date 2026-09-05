# Plan: documentation information architecture

**Baseline:** site as deployed at v4.0.2 (2026-08-19) · **Authority:** `docs/spec.md` §27-36, §69-80
**Status:** audit and proposal, partly implemented on this branch.

| Part | State |
|---|---|
| Audit, sitemap, migration plan, ownership matrix (§1-4) | Proposal only |
| `<Contribute />` component (§5) | Built: `docs-site/components/docs/contribute.tsx` |
| Issue template (§6) | Built: `.github/ISSUE_TEMPLATE/docs_contribution.yml` |
| Backlog (§7) | Proposal, except the four issues below |
| First placements | #350 `/jack`, #351 `/meet-agentdeck/quickstart`, #352 `/resources/known-issues`, #353 `/jack/notes` |

No route moved. No stub page rewritten. The four pages carrying `<Contribute />` are the four that
were already substantive, which is the §5 rule applied rather than an exception to it.

## The headline

The site is not mis-organised. It is unwritten.

33 pages, 1,476 lines. Four generated reference pages carry 759 of them. Six hand-written pages
carry real content: `known-issues`, `quickstart`, `jack`, `jack/notes`, `overview` and the landing
page. The remaining 23 are two-sentence placeholders, three of which reach a single code block.
Every one of the 23 states a definition and stops.

An IA already exists and is binding: `docs/spec.md` §28. The shipped nav does not implement it, and
§78 forbids incrementally polishing what shipped. So deliverable 2 below is that IA, pruned by the
spec's own rule that a nav item must represent a real supported capability, not a redesign of it.

Three defects are worse than thinness because a reader who trusts them is wrong:

| Page | Claim | Reality |
|---|---|---|
| `/reference/events` | kinds `message.delta`, `tool.call`, `tool.result` | none exist; `KNOWN_KINDS` has 21 kinds, page lists 7 |
| `/runs-and-control/lifecycle-and-control` | states `QUEUED`, `WAITING`, uppercase | `RunStatus` is `running, paused, waiting_answer, completed, failed, cancelled`; no `QUEUED` |
| `/meet-agentdeck/quickstart` | output `agent.message`, `Status: COMPLETED` | no such kind; `run.status` is `async def`, so the line prints a bound method |

---

## 1. Current-site audit

`Serves` answers: does the content do the job the title promises. `Core/Ref/Guide/Ex/Comm` is the
classification carried into deliverable 4.

### Landing and Start

| Route | Nav group | Lines | Purpose it should serve | Serves | Problem | Verdict |
|---|---|---|---|---|---|---|
| `/` | hidden | 30 | Make the argument, once | Yes | none | Keep |
| `/meet-agentdeck/overview` | Meet AgentDeck | 44 | What it is, in two minutes | Partly | no code, "production runtime and harness" is not the landing page's language | Move → `/start/overview`, rewrite |
| `/meet-agentdeck/quickstart` | Meet AgentDeck | 84 | Running agent in five minutes | Partly | fabricated transcript; `run.status` misuse; no runnable end state | Move → `/start/quickstart`, fix |
| `/meet-agentdeck/mental-model` | Meet AgentDeck | 15 | The one model everything hangs off | No | four bullets restating the overview | Move → `/start/mental-model`, rewrite |

### Build Your Deck

| Route | Lines | Serves | Problem | Verdict |
|---|---|---|---|---|
| `/build-your-deck/agents` | 20 | No | one declaration, 9 of `Agent`'s 12 parameters undocumented, including `handoffs`, `hooks`, `output_type`, `skills`, `mcp` | Move → `/build/agents` |
| `/build-your-deck/tools` | 19 | No | no `Context` tool, no raising tool, no return-type contract | Move → `/build/tools` |
| `/build-your-deck/workflows` | 13 | No | `Workflow(name=...)` alone, no graph, no state, no interrupt | Move → `/build/workflows` |
| `/build-your-deck/skills` | 7 | No | no `SKILL.md`, no layout, no example | Move → `/build/skills` |
| `/build-your-deck/context` | 7 | No | the concept the whole tool story depends on, in two sentences | Move → `/compose/context` |
| `/build-your-deck/deck` | 19 | Partly | both front doors shown, nothing about `build()`, lifecycle or validation | Move → `/compose/deck` |

Group name is wrong: `deck` and `context` are composition, not authoring, and the group title asks
the reader to build a Deck on a page where Deck is last.

### Runs & Control

| Route | Lines | Serves | Problem | Verdict |
|---|---|---|---|---|
| `/runs-and-control/runs` | 15 | Partly | no run identity, no `key`, no namespace, no rehydration | Move → `/run/runs` |
| `/runs-and-control/sessions` | 7 | No | two sentences; session-busy semantics undocumented | Move → `/run/sessions` |
| `/runs-and-control/events` | 10 | No | one snippet; envelope, ordering and `from_seq` absent | Move → `/run/events` |
| `/runs-and-control/lifecycle-and-control` | 7 | No | states are wrong (see headline) | Merge → `/run/control` |
| `/runs-and-control/pause-resume` | 10 | No | safe points, the thing that makes pause correct, unmentioned | Merge → `/run/control` |
| `/runs-and-control/human-input` | 11 | Partly | shows `answer()`, never says the node interprets the value | Move → `/run/human-input` |

`lifecycle-and-control` and `pause-resume` are one state machine split across two pages, and neither
page is complete enough to say which owns it.

### Integrations

| Route | Lines | Serves | Problem | Verdict |
|---|---|---|---|---|
| `/integrations/openai-agents-sdk` | 7 | No | no code, no native-access escape hatch | Keep route, rewrite |
| `/integrations/langgraph` | 7 | No | no code; `existing-langgraph-agent` example not linked | Keep route, rewrite |
| `/integrations/mcp` | 7 | No | no server config, no transport, no failure mode | Keep route, rewrite |
| `/integrations/existing-agents` | 7 | No | duplicates the two pages above without adding a mechanism | Move → `/compose/existing-agents` |

`existing-agents` overlaps both SDK pages and owns nothing they do not.

### Examples, Jack, Reference, Resources

| Route | Lines | Serves | Problem | Verdict |
|---|---|---|---|---|
| `/examples` | 12 | No | four examples named, none linked, none has a page; a fifth (`jack`) exists in the repo | Split → `/examples/*` |
| `/jack` | 111 | Yes | none | Move → `/guides/jack` |
| `/jack/notes` | 88 | Yes | none | Move → `/guides/jack/notes` |
| `/reference/python-api` | 9 | No | 3 of 13 exports listed; error taxonomy absent | Rewrite, generate |
| `/reference/deck` | 548 | Yes | generated | Keep |
| `/reference/run` | 12 | Partly | hand-written beside a generated sibling; drifts by construction | Merge → generated |
| `/reference/events` | 13 | No | wrong kinds (see headline) | Rewrite, generate from `KNOWN_KINDS` |
| `/reference/cli` | 50 | Yes | generated | Keep |
| `/reference/settings` | 98 | Yes | generated | Keep |
| `/resources/changelog` | 63 | Yes | generated | Keep |
| `/resources/migration-guides` | 8 | No | two bullets; 4.0.0 was a breaking release with a full Upgrading section in `CHANGELOG.md` | Rewrite |
| `/resources/troubleshooting` | 8 | No | two entries | Move → `/operate/troubleshooting` |
| `/resources/known-issues` | 110 | Yes | says "open against v4.0.0", now two releases stale | Keep, add version check |

### Structural findings

| # | Finding | Evidence |
|---|---|---|
| S1 | Six category roots serve a raw directory listing | `/meet-agentdeck/`, `/build-your-deck/`, `/runs-and-control/`, `/integrations/`, `/reference/`, `/resources/` return 200 with `<title>Files within out/...</title>`; `/examples/` and `/jack/` render because they have a page |
| S2 | Four groups have an index page, with no rule for which do | `index.mdx` exists in `content/`, `content/bindings/`, `content/examples/` and `content/jack/` |
| S3 | Reference is half generated, half hand-written, with no marker | `deck`/`cli`/`settings`/`changelog` generated; `python-api`/`run`/`events` hand-written and already wrong |
| S4 | Conceptual material sits in Reference | `/reference/run` explains what a Run is; `/run/runs` should |
| S5 | Reference material sits in concept pages | `/runs-and-control/lifecycle-and-control` is a state table |
| S6 | Nav groups are branded, routes are not stable | "Meet AgentDeck", "Build Your Deck", "Runs & Control" read as marketing; spec §27 names them Start Here / Build / Compose / Run |
| S7 | All four open docs GFIs are stale | #241 names `add-a-tool` and #246 names `serve-over-http`, neither of which is a route; #235 and #254 quote the removed v3 API (`deck.answer(run_id, value)`) |
| S8 | `Reporter` has no page anywhere | `agentdeck/core/reporting.py`, 3 of 21 event kinds (`progress.reported`, `status.reported`, `usage.reported`) |
| S9 | Observers have no page anywhere | `agentdeck/observers.py`, `core/ports/sink.py` |
| S10 | Durability and recovery have no page | `AGENTDECK_EVENTS` vs `AGENTDECK_CHECKPOINT` split is a documented trap in Known Issues and nowhere else |
| S11 | Serving has no page | `Deck.asgi()`, `agentdeck/surfaces/serve/` |
| S12 | Terminology drifts across pages | "harness", "runtime", "production runtime", "foundation" for one product; `WAITING` vs `waiting_answer` |
| S13 | `llms-full.txt` lags one generator run | `_generated_pages()` computes all five outputs before writing any |

Missing concepts, ranked by how badly a reader is left without them: Reporter, observers and
observability, durability and recovery, serving, deployment, invocation and nesting, handoffs,
run identity and idempotency keys, namespaces, error taxonomy, model configuration.

---

## 2. Proposed final sitemap

Nine groups. Eight are `docs/spec.md` §28 with dead entries pruned; **Guides** is the one addition,
and it exists because journey step 9 (advanced patterns) and the entire community contribution
surface have no home in the spec's IA.

```text
/                                  landing
/start/                            Start Here
    overview                       what AgentDeck is, in two minutes
    quickstart                     a running agent in five
    mental-model                   you own intent, AgentDeck owns machinery
/build/                            Build
    agents                         declaring an agent, and every knob on it
    tools                          plain functions, Context, failure
    skills                         SKILL.md, discovery, attachment
    workflows                      graphs, state, interrupts, durability
/compose/                          Compose
    deck                           the composition root and build()
    context                        typed application data through a run
    invocation                     agents inside workflows, nesting, handoffs
    existing-agents                wrapping what you already have
/run/                              Run
    runs                           run identity, start, await, rehydrate
    sessions                       history across turns, and session-busy
    events                         the ordered log and how to read it
    reports                        progress and status from inside the work
    control                        pause, resume, cancel, safe points
    human-input                    interrupts and answering them
/operate/                          Operate
    configuration                  settings layering and AGENTDECK_*
    persistence                    stores, checkpointers, what survives
    serving                        Deck.asgi() and the HTTP/SSE surface
    observability                  observers, sinks, telemetry
    deployment                     processes, workers, security posture
    troubleshooting                symptom to cause
/integrations/                     Integrations
    openai-agents                  the agent engine
    langgraph                      the workflow engine
    mcp                            external tools over MCP
    models                         endpoints, gateways, model settings
/guides/                           Guides
    jack                           the documentation agent, end to end
    jack/notes                     the decisions behind it
    <task guides>                  see backlog
/examples/                         Examples
    index                          the runnable catalog
    <one page per examples/*>
/reference/                        Reference
    python-api                     every export, generated
    deck                           Deck API, generated
    run                            Run API, generated
    events                         event types, generated from KNOWN_KINDS
    cli                            generated
    settings                       generated
    ports                          the extension contracts
/resources/                        Resources
    changelog                      generated
    migration-guides               per-major upgrade path
    known-issues                   what will surprise you now
```

### What belongs in each group, and what does not

| Group | Belongs | Does not belong | Why it exists |
|---|---|---|---|
| Start Here | The three pages a first-time reader reads in order | Anything a returning reader looks up | The only sequential part of the site |
| Build | What you declare: agent, tool, skill, workflow | How declarations are assembled or run | One page per authoring primitive, and no more |
| Compose | How declarations become one application | Any single primitive's own options | Deck, Context and Invocation are only meaningful across primitives |
| Run | Everything true of an execution in flight | Anything about declaring the thing that runs | The reader is now asking about a Run, not an Agent |
| Operate | Everything true once it is someone else's problem at 3am | Concepts a developer needs before deploying | Production concerns kept out of the learning path |
| Integrations | One page per external technology AgentDeck adapts | AgentDeck's own behavior | An adapter boundary maps to a page cleanly |
| Guides | Task-oriented, end to end, one goal per page | Any authoritative contract | Where a reader goes after the concepts, and where contributors can safely write |
| Examples | Runnable code in `examples/`, one page each | Prose that is not backed by a directory | Each page is provably true because CI runs it |
| Reference | Generated, exact, machine-checked | Explanation of why | Reference drifts unless generated |
| Resources | History and current defects | Anything a reader needs to build | Chronological, not conceptual |

### Rulings

| Ruling | Reason |
|---|---|
| No `Choose Your Path` page | Nine groups are self-describing; the Overview ends with three links |
| No `Subagents` page | No such capability; `subagent` appears nowhere in `agentdeck/` |
| No `Hosted Tools` group entry | Only `web_search` exists; it is a section in `/build/tools` |
| Handoffs fold into `/build/agents` | `handoffs` is an `Agent` parameter, and a page per parameter is the source-tree mirror the brief forbids |
| `Suspend & Resume` and `Run Control` merge into `/run/control` | One state machine, one page |
| `Human Interaction` moves from Build to Run | The reader meets it when a run stops, not when they declare one |
| `Security` folds into `/operate/deployment` | `SECURITY.md` is authoritative; a second copy would drift |
| `Observability Integrations` folds into `/operate/observability` | Langfuse is one sink, not a section |
| No `Release Notes` page | It would duplicate the generated changelog |
| `Recipes` folds into Guides | Two names for task-oriented prose is one too many |
| Examples stays top-level, not under Resources | It is a learning surface and the strongest contribution target |
| Every group root gets an `index.mdx` | Fixes S1 and S2 in one move |

---

## 3. Route migration plan

| Current route | Action | Target |
|---|---|---|
| `/` | Keep | `/` |
| `/meet-agentdeck/` | Redirect | `/start/` |
| `/meet-agentdeck/overview` | Move + rewrite | `/start/overview` |
| `/meet-agentdeck/quickstart` | Move + fix | `/start/quickstart` |
| `/meet-agentdeck/mental-model` | Move + rewrite | `/start/mental-model` |
| `/build-your-deck/` | Redirect | `/build/` |
| `/build-your-deck/agents` | Move | `/build/agents` |
| `/build-your-deck/tools` | Move | `/build/tools` |
| `/build-your-deck/skills` | Move | `/build/skills` |
| `/build-your-deck/workflows` | Move | `/build/workflows` |
| `/build-your-deck/deck` | Move | `/compose/deck` |
| `/build-your-deck/context` | Move | `/compose/context` |
| `/runs-and-control/` | Redirect | `/run/` |
| `/runs-and-control/runs` | Move | `/run/runs` |
| `/runs-and-control/sessions` | Move | `/run/sessions` |
| `/runs-and-control/events` | Move | `/run/events` |
| `/runs-and-control/lifecycle-and-control` | Merge | `/run/control` |
| `/runs-and-control/pause-resume` | Merge | `/run/control` |
| `/runs-and-control/human-input` | Move | `/run/human-input` |
| `/integrations/openai-agents-sdk` | Rename | `/integrations/openai-agents` |
| `/integrations/langgraph` | Keep | `/integrations/langgraph` |
| `/integrations/mcp` | Keep | `/integrations/mcp` |
| `/integrations/existing-agents` | Move | `/compose/existing-agents` |
| `/examples` | Split | `/examples/` + one page per `examples/*` |
| `/jack` | Move | `/guides/jack` |
| `/jack/notes` | Move | `/guides/jack/notes` |
| `/reference/python-api` | Keep, generate | `/reference/python-api` |
| `/reference/deck` | Keep | `/reference/deck` |
| `/reference/run` | Keep, generate | `/reference/run` |
| `/reference/events` | Keep, generate | `/reference/events` |
| `/reference/cli` | Keep | `/reference/cli` |
| `/reference/settings` | Keep | `/reference/settings` |
| `/resources/changelog` | Keep | `/resources/changelog` |
| `/resources/migration-guides` | Keep, rewrite | `/resources/migration-guides` |
| `/resources/known-issues` | Keep | `/resources/known-issues` |
| `/resources/troubleshooting` | Move | `/operate/troubleshooting` |
| n/a | New | `/start/`, `/build/`, `/compose/`, `/run/`, `/operate/`, `/integrations/`, `/guides/`, `/reference/`, `/resources/` index pages |
| n/a | New | `/run/reports`, `/operate/*`, `/compose/invocation`, `/integrations/models`, `/reference/ports` |

Redirects are a build concern, not a content one: the site is `output: 'export'`, so every retired
route needs an emitted HTML page carrying a canonical link and a meta refresh. 24 routes change; none
may 404, because `llms.txt`, `llms-full.txt`, the README, four open issues and Google all point at
the current set.

---

## 4. Page ownership matrix

`Comm` marks a page an outside contributor can extend **after** the project has written its
authoritative part. No page is `Comm` on its own.

| Page | Class | Comm | One-sentence responsibility |
|---|---|---|---|
| `/start/overview` | Core | no | What AgentDeck is and what it removes from your codebase. |
| `/start/quickstart` | Core | no | Get one agent running and name what you just used. |
| `/start/mental-model` | Core | no | You own intent, AgentDeck owns machinery. |
| `/build/agents` | Core | yes | Declare an agent and every option that changes how it decides. |
| `/build/tools` | Core | yes | Turn a plain function into something a model may call. |
| `/build/skills` | Core | yes | Package reusable instructions as files an agent loads. |
| `/build/workflows` | Core | yes | Declare a deterministic graph with state and interrupts. |
| `/compose/deck` | Core | no | Assemble a catalog and validate it before the first turn. |
| `/compose/context` | Core | yes | Give tools typed application data the model never sees. |
| `/compose/invocation` | Core | no | Call one invocable from another and keep one execution tree. |
| `/compose/existing-agents` | Guide | yes | Run an agent you already wrote without rewriting it. |
| `/run/runs` | Core | no | A Run is addressable, awaitable and rehydratable. |
| `/run/sessions` | Core | yes | Keep conversation state across turns, and what happens when one is busy. |
| `/run/events` | Core | no | Read the one ordered log that says what happened. |
| `/run/reports` | Core | yes | Send progress and status from inside the work. |
| `/run/control` | Core | no | Pause, resume and cancel at documented safe points. |
| `/run/human-input` | Core | yes | Park a run on a person and resume it with their answer. |
| `/operate/configuration` | Core | no | Where settings come from and which layer wins. |
| `/operate/persistence` | Core | yes | Choose stores, and know exactly what survives a restart. |
| `/operate/serving` | Core | yes | Expose a Deck over HTTP and SSE. |
| `/operate/observability` | Core | yes | Attach an observer and get runs into your telemetry. |
| `/operate/deployment` | Guide | yes | Run this in production without surprising yourself. |
| `/operate/troubleshooting` | Guide | yes | Symptom to cause to fix. |
| `/integrations/openai-agents` | Core | yes | How an Agent becomes an SDK agent, and how to reach the SDK. |
| `/integrations/langgraph` | Core | yes | How a Workflow becomes a graph, and how to reach LangGraph. |
| `/integrations/mcp` | Core | yes | Attach MCP servers and know what happens when one is down. |
| `/integrations/models` | Core | yes | Point AgentDeck at any OpenAI-compatible endpoint. |
| `/guides/jack` | Guide | no | What building a real application on AgentDeck looks like. |
| `/guides/jack/notes` | Guide | no | The decisions behind Jack and the alternatives they beat. |
| `/guides/*` (new) | Guide | yes | One goal, start to finish, one page. |
| `/examples/index` | Example | yes | Every runnable example and what each one proves. |
| `/examples/*` | Example | yes | What this example demonstrates, how to run it, what you see. |
| `/reference/python-api` | Reference | no | Every public export and what it raises. |
| `/reference/deck` | Reference | no | Every `Deck` method, generated. |
| `/reference/run` | Reference | no | Every `Run` method, generated. |
| `/reference/events` | Reference | no | Every event kind and payload, generated. |
| `/reference/cli` | Reference | no | Every command and flag, generated. |
| `/reference/settings` | Reference | no | Every setting and its default, generated. |
| `/reference/ports` | Reference | no | The contracts you implement to extend AgentDeck. |
| `/resources/changelog` | Reference | no | What changed, generated. |
| `/resources/migration-guides` | Reference | no | The exact edits each major release requires. |
| `/resources/known-issues` | Core | no | Defects in the current release that will surprise you. |

Totals across 42 rows: 25 Core, 9 Reference, 6 Guide, 2 Example families, 21 Comm-eligible.
No Core page is delegated. Every `Comm` page is the project's own text plus a scoped invitation.

---

## 5. Contribution component specification

### Behavior

One MDX component, `<Contribute />`, placed at the end of a page whose authoritative part is
already written. It never appears on a page that is not yet useful.

```mdx
<Contribute
  issue={412}
  difficulty="medium"
  scope="example"
  need="This page documents the Observer contract and shows a minimal sink, and nothing here shows one consuming a real run."
  because="A reader needs to see what a production observer actually does with the events."
/>
```

| Prop | Type | Purpose |
|---|---|---|
| `issue` | number | The one issue this invites; renders as `#412` and the link target |
| `difficulty` | `small \| medium \| advanced` | Sets the badge and tells the reader the size before they click |
| `scope` | `example \| guide \| diagram \| troubleshooting \| integration` | Sets the second half of the badge |
| `need` | string | What is missing, in one sentence, written per page |
| `because` | string | Why it helps, in one sentence, written per page |

No fetch, no build-time GitHub call, no backend. Issue state is not shown, because a stale "open"
badge is worse than none and the lifecycle below removes the component instead.

### Appearance

The card idiom already on the site: 12px radius with a 5px cut corner, `--brand-blue` rule on the
left, Ice ground in light and Slate in dark. Not a Callout, and never `type="warning"`. It reads as
a footer to a finished page, at the same weight as a "Next" link, never as a banner.

### Copy

```text
Improve this page                                    [ medium · example ]

This page documents the Observer contract and shows a minimal sink. It could
use a runnable example forwarding Run events into a logging backend, so a
reader can see what a production observer actually does with them.

Take this contribution  #412 →
```

Two sentences, fixed shape: what is missing, why it helps. `need` carries enough of what the page
already covers to make the gap legible, so the reader who never clicks still learns the page's
boundary.

### Rules

| Rule | Reason |
|---|---|
| Never on a page missing its authoritative part | The brief's rule, and the difference between an invitation and a TODO |
| Exactly one per page | Two invitations means the page is not finished |
| Links to one issue, never to a label listing | A first contribution needs a task, not a search |
| The `need` is written per page, not templated | A generic sentence tells a contributor nothing |
| Removed on merge, not left to rot | See lifecycle |

### Lifecycle

```text
gap identified → issue written (§6) → <Contribute /> added with the issue number
              → PR opened → PR merged → component removed in the same PR
              → optionally replaced with the next scoped gap
```

The removal is a line in the PR checklist, so the docs cannot advertise work that is done.

### Metadata

Props are the data model. A central registry would need to stay in sync with pages that already
name their own gap, and the brief asks for the simple version first.

---

## 6. GitHub issue template

New file `.github/ISSUE_TEMPLATE/docs_contribution.yml`, beside the existing `bug_report`,
`feature_request` and `finding` templates. Maintainer-facing: it is the form the project fills in
when opening a documentation contribution, not a form outside reporters use.

```yaml
name: Documentation contribution
description: A scoped documentation task, specified well enough for a first contribution.
title: "docs: "
labels: ["documentation", "area:docs"]
body:
  - type: markdown
    attributes:
      value: |
        Questions are welcome on this issue. Ask before you write if anything about
        AgentDeck's intent is unclear; a maintainer will answer, and that is not a
        sign the task was too hard.

  - type: textarea
    id: context
    attributes:
      label: Context
      description: The AgentDeck concept this documents and why the page exists. A contributor should not have to reverse-engineer the architecture to start.
    validations: { required: true }

  - type: textarea
    id: current-state
    attributes:
      label: Current state
      description: What is implemented, and what the page already says. Link the page and the source.
    validations: { required: true }

  - type: textarea
    id: missing
    attributes:
      label: What is missing
      description: "Precise. Not \"improve this page\". Name the artifact: a runnable example of X, a diagram of Y, a troubleshooting entry for Z."
    validations: { required: true }

  - type: textarea
    id: expected
    attributes:
      label: Expected contribution
      description: What to add, with a suggested structure or heading list.
    validations: { required: true }

  - type: textarea
    id: apis
    attributes:
      label: Relevant APIs and source files
      description: Classes, functions and protocols to read, with permalinks.
    validations: { required: true }

  - type: input
    id: files
    attributes:
      label: Files to change
      description: The exact MDX or example paths.
    validations: { required: true }

  - type: textarea
    id: acceptance
    attributes:
      label: Acceptance criteria
      value: |
        - [ ] Uses only public APIs from `agentdeck`
        - [ ] Any code shown runs against the current release
        - [ ] Does not duplicate the Quickstart
        - [ ] Includes the expected output
        - [ ] Links the related reference page
        - [ ] `make check` passes
        - [ ] `docs-site` builds
    validations: { required: true }

  - type: textarea
    id: out-of-scope
    attributes:
      label: Out of scope
      value: |
        - No API redesign
        - No new runtime behavior
        - No change to Run semantics
        - No navigation restructuring
        - No new abstraction unless agreed on this issue first
    validations: { required: true }

  - type: textarea
    id: verify
    attributes:
      label: How to verify locally
      value: |
        ```bash
        make install
        make check
        cd docs-site && npm install && npm run dev    # http://localhost:3040
        python -m <example_package>                   # if an example changed
        ```
    validations: { required: true }

  - type: dropdown
    id: difficulty
    attributes:
      label: Difficulty
      options: ["small", "medium", "advanced"]
    validations: { required: true }
```

### Labels

The existing taxonomy is enough: `documentation`, `area:docs`, `good first issue`, `help wanted`.
No new labels. Difficulty lives in the template field and in the `<Contribute />` badge, because a
three-value axis does not earn three labels.

`good first issue` goes on small and medium only. Advanced tasks get `help wanted`.

| Difficulty | Shape | `good first issue` |
|---|---|---|
| Small | Clarification, diagram, expected output, one troubleshooting entry | yes |
| Medium | One runnable guide or one integration example | yes |
| Advanced | A full application example or a protocol integration | no |

Before any of these are filed, #235, #241, #246 and #254 need retargeting: two name routes that do
not exist, and two quote the v3 `deck.answer(run_id, value)` that v4 removed.

---

## 7. Initial contribution backlog

Fifteen candidates. None fills a Core page's authoritative part; each extends a page the project has
already written. Ordered by usefulness.

| # | Page | Contribution | Diff. | Why useful | Expected output | GFI |
|---|---|---|---|---|---|---|
| 1 | `/operate/observability` | Runnable example: an Observer consuming Run events and forwarding structured progress to a logging backend | medium | Observers are documented nowhere today and are the main integration point for existing telemetry | `examples/observer-to-logs/` plus a page section with sample log lines | yes |
| 2 | `/run/reports` | Runnable example: a long tool emitting `progress.reported` and a consumer rendering a progress bar | medium | Reporter is 3 of 21 event kinds and has no example anywhere | `examples/progress-reporting/` and the terminal output it produces | yes |
| 3 | `/operate/troubleshooting` | Ten entries in symptom → cause → fix form, drawn from Known Issues and closed bugs | small | The page has two entries; this is the page people arrive at from a search engine | A table, one row per symptom | yes |
| 4 | `/build/tools` | Worked example of a tool that raises, and what the run does with it | small | Failure is the undocumented half of the tool contract | A section with the event sequence a raising tool produces | yes |
| 5 | `/run/events` | Diagram of the event envelope and the ordering guarantee | small | The envelope has eight fields and no page draws it | One mermaid diagram with a one-line caption | yes |
| 6 | `/operate/serving` | Guide: stream a Deck's events to a browser over SSE, end to end | medium | `examples/jack` does it, but no page teaches it in isolation | A guide page plus a minimal HTML client | yes |
| 7 | `/operate/persistence` | Guide: move from `memory://` to SQLite to Postgres, with what changes at each step | medium | The events/checkpoint split is a live trap in Known Issues | A guide with three configurations and what survives a restart in each | yes |
| 8 | `/integrations/mcp` | Runnable example attaching one public MCP server, and the behavior when it is unreachable | medium | MCP has seven lines and no code | `examples/mcp-tools/` and the failure output | yes |
| 9 | `/examples/*` | One page per existing example in `examples/`, each stating what it proves and its expected output | small | Five runnable examples ship and none has a page | Five short pages, linked from the catalog | yes |
| 10 | `/start/mental-model` | Diagram: intent above the line, machinery below, one Run crossing it | small | The page is four bullets and the site's whole argument rests on it | One diagram, no new prose | yes |
| 11 | `/operate/deployment` | Recipe: a Deck behind a process manager, with health check and graceful shutdown | medium | Nothing documents running this as a service | A guide with unit file and shutdown semantics | yes |
| 12 | `/compose/invocation` | Diagram of a nested execution tree, one run, three levels | small | Nesting is a core claim on the landing page and undrawn in the docs | One diagram matching real event output | yes |
| 13 | `/integrations/langgraph` | Walkthrough of `examples/existing-langgraph-agent`, naming what AgentDeck added | medium | The strongest adoption argument, currently seven lines | A guide page tied to the shipped example | yes |
| 14 | `/guides/` | End-to-end application example that is not a chat agent, for instance a batch pipeline over a Deck | advanced | Every example is conversational; the batch path is a stated use case | A new `examples/` project and its guide | no |
| 15 | `/integrations/models` | Recipes for three OpenAI-compatible endpoints: a gateway, vLLM, Ollama | medium | `OPENAI_BASE_URL` is one README sentence and a common first blocker | Three configuration blocks, each verified | yes |

Deliberately not on this list: any Core page's first draft, the Reporter contract, the Run
lifecycle, the Deck build sequence, the error taxonomy. Those are P1 and ours.

---

## 8. Priority order

### P0: structural, before any content work

| # | Task | Why first |
|---|---|---|
| 0.1 | Group index pages, so no route serves a directory listing | S1: three roots currently render `Files within out/...` |
| 0.2 | Fix the three wrong pages: event kinds, run states, quickstart transcript | Wrong beats thin; a reader who trusts these is misled |
| 0.3 | Land the nav and route layout in §2 with redirects for all 20 moved routes | Content written against the old tree has to move twice otherwise |
| 0.4 | Generate `/reference/events`, `/reference/run`, `/reference/python-api` | Hand-written reference beside generated reference drifts by construction (S3) |
| 0.5 | Fix the one-run lag in `scripts/generate_docs_reference.py` | S13; every release ships a stale `llms-full.txt` |
| 0.6 | Retarget #235, #241, #246, #254 | Two name routes that never existed, two quote a removed API |
| 0.7 | Version-stamp Known Issues against the current release | It claims v4.0.0 at v4.0.2 |

### P1: Core docs we own

In this order, because each is a prerequisite of the next: mental model, Deck, context, runs,
events, control, human input, agents, tools, workflows, skills, invocation, reports, sessions,
configuration, persistence, serving, observability.

Reports, observability, persistence and serving are the four with no page at all today and the four
the product's own pitch leans on hardest.

### P2: community contributions

Backlog items 1-13 and 15, once their host page's authoritative part exists. An item is not filed
until the page it attaches to is written; the `<Contribute />` rule and the issue both depend on it.

### P3: optional expansion

Backlog item 14, per-example deep dives beyond the first page each, framework-specific walkthroughs,
recipes beyond the first set, and translations.

---

## Verification of claims in this document

| Claim | Checked by |
|---|---|
| 21 event kinds | `python -c "from agentdeck.core.events import KNOWN_KINDS; print(len(KNOWN_KINDS))"` |
| Run statuses | `RunStatus` in `agentdeck/core/status.py` |
| `run.status` is async | `agentdeck/deck.py:1176` |
| No subagent capability | `grep -r subagent agentdeck/` returns nothing |
| Category roots list directories | `curl -s https://agentdecksdk.com/reference/` |
| Page line counts | `wc -l docs-site/content/**/*.mdx` |
| Agent has 12 parameters | `inspect.signature(Agent)` |
