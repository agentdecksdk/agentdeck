# Documentation Standards

**Status:** Binding for every page under `docs-site/content/`.

## 1. Scope boundary

The public docs answer one question: **how do I use AgentDeck today.**

They are not a record of how it was built. Everything else has a home:

| Content | Home |
|---|---|
| User-facing documentation | `docs-site/content/` |
| Architecture and rulings | `docs/design/` |
| Delivery plans, milestones, retrospectives | `agentdeck-internal:planning/` |
| Active discussion | GitHub issues |
| History | `CHANGELOG.md` and migration guides |

The navigation must never contain internal architecture debates, rejected proposals, delivery plans, milestone tracking, implementation diaries, ADR-level reasoning or contributor-only detail. Those may be public in the repository; they are not product documentation.

## 2. Principles

| | Rule |
|---|---|
| Answer first | Open with the useful answer. `A Run represents one execution of an AgentDeck invocable`, not `AgentDeck's runtime abstraction evolved in response to`. |
| Code before theory | Show normal operation, then explain the architecture behind it. |
| Progressive disclosure | What, why, minimal usage, common behavior, advanced behavior, reference. Internals last, and only if truly necessary. |
| One page, one main idea | A page that teaches runs and sessions and event sourcing and persistence at once is several pages. |
| Current truth only | Document the current recommended API. "Before v3 this was called" belongs in a migration guide. |
| Teach the recommended path | Every capability has one preferred pattern. Alternatives come later, labelled as alternatives. |
| No internals without user value | Timer sweeps, checkpoint internals, sequence allocation and lease renewal are not introductory workflow documentation. |

## 3. Page grammar

Every page type has a fixed shape. Deviating from it is a review comment.

| Page type | Shape |
|---|---|
| Concept | title, one-sentence definition, when to use it, minimal example, how it works, common patterns, important behavior, related concepts, next step |
| Guide | goal, prerequisites, implementation, run it, expected result, how it works, next steps |
| Integration | what it gives you, install, minimal integration, AgentDeck behavior, native access, supported capabilities, limitations, next steps |
| Reference | signature, parameters, returns, exceptions, behavior, example, related APIs |

## 4. Writing

Voice: confident, technical, concise, practical, calm, precise.

Banned unless a concrete statement immediately substantiates it: revolutionary, cutting-edge, incredibly powerful, seamless, next-generation.

> Use a workflow when execution has explicit steps and state.

not

> AgentDeck's powerful workflow engine unlocks advanced orchestration possibilities.

One to three sentences per paragraph. Headings carry information rather than decorate. Lists only where structure improves scanning: not every sentence is a bullet.

## 5. Code

Code is the primary communication medium. Examples are short, complete enough to understand, on the recommended API, free of unrelated setup, and executable wherever practical. No hypothetical APIs.

A page does not present five equivalent approaches unless comparison is the point of the page.

Reuse the canonical set rather than maintaining a slightly different basic example per page: `basic-agent`, `tool`, `workflow`, `skill`, `handoff`, `human-input`, `run-control`, `existing-agent`, `mcp`, `persistence`, `observability`.

## 6. Source of truth

Never duplicate by hand what another source can generate.

| Kind | Authority |
|---|---|
| Conceptual behavior | the page itself |
| API shape | source code and docstrings |
| Examples | the executable canonical examples |
| History | `CHANGELOG.md`, migration guides |
| Internal decisions | ADRs and design docs |

## 7. Maturity and versions

Public capabilities carry **Stable** (compatibility preserved), **Beta** (supported, API may evolve) or **Experimental** (expected to change). Internal implementation carries no badge because it is not public documentation. Badges are visible, not overwhelming.

Maintain `Latest` only, until a stable line has users who depend on it. Old versions do not pollute default search results.

## 8. Documentation is part of the API change

A public API change is incomplete until its documentation changes in the same PR: the concept page, the affected guide or example, the reference docstring, and a migration note when breaking. Documentation is not a cleanup task after release.

`scripts/check_docs_impact.py` enforces this: a page declares its `docs_sources`, and a PR touching one must update the page or name it as reviewed.

## 9. CI protects the examples

Where practical, extract examples, type-check them, execute them, validate internal links, detect missing pages and validate reference generation. Breaking a canonical example breaks CI. That makes documentation a compatibility surface rather than static prose.

## 10. Review checklist

Before merging a page:

- Who is it for, what question does it answer, and does the answer appear quickly?
- Is there unnecessary history or implementation detail? Can prose be deleted? Is there a minimal example?
- Is this already documented elsewhere? Does it introduce a concept it does not need?
- Does the example use the recommended API, and does it run?
- Is this the right location, and does the page deserve to exist?

## The delete test

> If this page disappeared, what user task would become harder?

If the answer is unclear, the page is not needed.
