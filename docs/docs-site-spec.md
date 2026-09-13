# Documentation site specification

**Status:** Target information architecture and visual design for `docs-site/`. Not yet implemented; `docs/delivery/plan-docs-ia.md` audits the gap and #728 is closing it.
**Scope:** What the documentation site should contain, how it should be organised, and how it should look.

The binding rules for writing any single page are `docs/engineering/documentation.md`. This file is the shape of the whole, not the law for a part.

---

# 21. Documentation Mission

AgentDeck documentation must embody the same philosophy as AgentDeck itself.

AgentDeck may contain a sophisticated runtime.

The documentation must make that runtime understandable without requiring developers to understand its implementation.

The documentation should make users think:

> "This is straightforward."

Not:

> "This framework must be extremely complicated."

The complexity should become visible progressively, only when the user's task requires it.

---

# 22. Docs North Star

> **AgentDeck may be sophisticated underneath. Its documentation must make it feel simple.**

The documentation system should optimize for four outcomes:

1. A new developer understands AgentDeck within 30 seconds.
2. A new developer executes something useful within minutes.
3. An existing agent-framework user understands how AgentDeck fits into their stack.
4. An experienced AgentDeck user can find an exact technical answer quickly.

---

# 23. Primary User Modes

The site must explicitly support three modes.

## 23.1 Learning

```text
I do not know AgentDeck.
↓
Explain the model.
↓
Show me something working.
```

## 23.2 Building

```text
I know the basics.
↓
I want to accomplish a task.
```

## 23.3 Lookup

```text
I already use AgentDeck.
↓
Tell me exactly how this API/configuration/event behaves.
```

These modes must not be mixed into a single page type.

---

# 24. Primary Audiences

## New Agent Developer

Wants:

* first agent,
* first tool,
* first workflow,
* minimal concepts.

## Existing Framework User

Already uses something such as OpenAI Agents or LangGraph.

Wants:

* integration,
* wrapping,
* interoperability,
* native escape hatches.

## Application Engineer

Building a real application.

Wants:

* context,
* sessions,
* control,
* persistence,
* events,
* deployment.

## Platform / Infrastructure Engineer

Wants:

* lifecycle,
* observability,
* operational behavior,
* stores,
* protocols,
* scaling,
* reliability.

## Contributor / Maintainer

Wants:

* internal architecture,
* RFCs,
* ADRs,
* implementation reasoning.

This final audience should primarily use repository design documentation rather than the public user documentation.

---

# 27. Documentation Architecture

The primary documentation navigation shall be:

```text
START HERE

BUILD

COMPOSE

RUN

OPERATE

INTEGRATIONS

REFERENCE

RESOURCES
```

This structure is task-oriented rather than repository-oriented.

---

# 28. Full Information Architecture

```text
AgentDeck
│
├── Start Here
│   ├── Overview
│   ├── Quickstart
│   ├── Mental Model
│   └── Choose Your Path
│
├── Build
│   ├── Agents
│   ├── Tools
│   ├── Workflows
│   ├── Skills
│   └── Human Interaction
│
├── Compose
│   ├── Deck
│   ├── Context
│   ├── Invocation
│   ├── Handoffs
│   ├── Subagents
│   └── Existing Agents
│
├── Run
│   ├── Runs
│   ├── Sessions
│   ├── Events
│   ├── Reports
│   ├── Suspend & Resume
│   └── Run Control
│
├── Operate
│   ├── Configuration
│   ├── Persistence
│   ├── Serving
│   ├── Observability
│   ├── Deployment
│   ├── Security
│   └── Troubleshooting
│
├── Integrations
│   ├── Models
│   ├── OpenAI Agents
│   ├── LangGraph
│   ├── MCP
│   ├── Hosted Tools
│   └── Observability Integrations
│
├── Reference
│   ├── Python API
│   ├── CLI
│   ├── Configuration
│   ├── Event Types
│   └── Protocols
│
└── Resources
    ├── Examples
    ├── Recipes
    ├── Migration Guides
    ├── Changelog
    └── Release Notes
```

Items should only be visible when they represent real supported capabilities.

Do not create empty documentation architecture for roadmap items.

---

# 29. Start Here

## Overview

Purpose:

> Explain AgentDeck in less than two minutes.

Must answer:

* What is AgentDeck?
* What problem does it solve?
* What are its main primitives?
* Why would I use it?
* What existing systems can I keep using?

Target length:

Approximately one screen of explanation plus a small visual and code example.

---

## Quickstart

Target:

> Working AgentDeck execution in approximately five minutes.

Sequence:

```text
Install
↓
Create an agent
↓
Create a Deck
↓
Run it
↓
Add one tool
```

Do not introduce:

* persistence configuration,
* observers,
* event schemas,
* production deployment,
* internal runtime architecture.

---

## Mental Model

This should be one of the most important pages on the site.

It should establish:

```text
Agents     Tools     Skills     Workflows
   \         |         |          /
                Deck
                 |
                Run
                 |
          AgentDeck Runtime
          /      |      \
      Events  Control  Persistence
```

The user should leave knowing:

* what is defined,
* what is composed,
* what is executed,
* what AgentDeck manages.

---

## Choose Your Path

Provide clear entry points:

* Build from scratch
* Bring an existing agent
* Build a workflow
* Build a production system

---

# 30. Build Section

The Build section teaches individual building blocks.

Each page should be usable before the reader understands the entire runtime.

---

## Agents

Teach:

* what an Agent is,
* minimal construction,
* instructions,
* tools,
* context,
* composition,
* native engine access where relevant.

Do not duplicate complete constructor reference.

---

## Tools

Teach:

* plain functions,
* AgentDeck tool model,
* context integration,
* execution behavior,
* existing compatible tool types,
* when custom AgentDeck tooling is useful.

The page should reinforce:

> Use what you already have when possible.

---

## Workflows

Start with:

> Use a workflow when execution has explicit steps, state, or control flow.

Teach incrementally:

1. minimal workflow,
2. state,
3. nodes,
4. invoking agents/tools,
5. durable workflows,
6. interruption where appropriate.

Implementation details move elsewhere.

---

## Skills

Teach:

* what a Skill is,
* when it differs from a Tool,
* how to load/use one,
* expected structure,
* composition.

Avoid protocol details until necessary.

---

## Human Interaction

Provide one conceptual home for:

* asking users for information,
* interrupts,
* waiting,
* answering,
* human approval,
* resumability.

Do not force users to assemble the model from several unrelated pages.

---

# 31. Compose Section

This should communicate one of AgentDeck's strongest differentiators.

---

## Deck

The page should explain:

> `Deck` is the composition root of an AgentDeck application.

Show a small example first.

Then explain:

* agents,
* workflows,
* skills,
* MCP,
* build/open lifecycle,
* catalog behavior.

Avoid internal ownership implementation unless operationally relevant.

---

## Context

Explain the public mental model first.

Possible structure:

```text
Application data
Runtime services
Invocation context
Reporting/control access
```

Do not begin with dependency-injection theory.

---

## Invocation

Teach how one AgentDeck component invokes another.

This becomes the conceptual home for `ctx.invoke()` and equivalent invocation behavior.

---

## Handoffs

Explain:

* when to hand off,
* what is transferred,
* how AgentDeck represents the transition,
* practical examples.

---

## Existing Agents

This is a first-class page.

Headline:

> **Bring the agents you already have.**

Supported systems appear explicitly.

Examples may include:

* OpenAI Agents
* LangGraph
* other supported runtimes
* custom Python integration

Every integration must explain what AgentDeck adds and what remains native.

---

# 32. Run Section

This section should show the public lifecycle first and defer machinery.

---

## Runs

Primary public model:

```python
run = await deck.runs.start("agent", input)

await run.status()
await run.pause()
await run.resume()
await run.cancel()

result = await run
```

The page should explain:

```text
START
→ RUNNING
→ COMPLETED
```

Then introduce alternative states:

```text
RUNNING
→ PAUSED
→ RUNNING

RUNNING
→ WAITING_ANSWER
→ RUNNING

RUNNING
→ CANCELLED

RUNNING
→ FAILED
```

The reader should not need to understand the underlying event store to manage a run.

---

## Sessions

Explain sessions in terms of user value:

> Sessions associate related turns with shared conversational/execution history.

Then:

* create/use session,
* concurrency behavior,
* relationship to Runs.

---

## Events

Start with:

> AgentDeck exposes one canonical event stream describing what happened during execution.

Then:

```python
async for event in run.events():
    ...
```

Only later explain:

* replay,
* filtering,
* sequence,
* schemas.

---

## Reports

Make the difference explicit:

```text
Events
= runtime facts

Reports
= contextual information emitted during execution
```

Include practical uses.

---

## Suspend & Resume

Unify:

* pause,
* waiting for external/user input,
* answer,
* resume,
* timers where appropriate.

Show the user-facing behavior before implementation mechanics.

---

## Run Control

Advanced page.

May explain:

* cancellation,
* safe points,
* control signals,
* concurrency rules,
* races,
* state preconditions.

This is where the deeper lifecycle model belongs.

---

# 33. Operate Section

Production details should exist, but should never dominate onboarding.

---

## Configuration

Show:

1. defaults,
2. common configuration,
3. environment configuration,
4. full reference link.

---

## Persistence

Teach:

* what becomes durable,
* when persistence matters,
* supported stores,
* recommended production posture.

Separate event persistence from framework-specific checkpointing where necessary.

---

## Serving

Teach the primary HTTP/ASGI path.

Minimal example first.

---

## Observability

Teach:

* what can be observed,
* events,
* reports,
* observers,
* external integrations.

---

## Deployment

Focus on deployment models AgentDeck actually supports.

Do not pretend roadmap capabilities already exist.

---

## Troubleshooting

Organize by symptom.

Examples:

```text
My run is stuck
My session is busy
My workflow will not resume
My context type does not match
My MCP server is unavailable
```

Each answer should lead to action.

---

# 34. Integrations

Integrations should be highly visible because interoperability is a core AgentDeck product property.

Each integration page follows:

```text
What this integration gives you

Install

Minimal example

What AgentDeck manages

What remains native

Access the native object

Supported capabilities

Limitations

Next step
```

Do not hide limitations.

Trust is more valuable than claiming universal compatibility.

---

# 35. Reference

Reference should optimize for lookup, not teaching.

The Python API should be generated from the source wherever practical.

Reference pages should contain:

```text
symbol

signature

parameters

return value

exceptions

brief behavior

minimal example

related symbols

source link
```

Do not repeat conceptual essays.

---

# 36. Resources

## Examples

Examples are a first-class product surface.

Suggested categories:

```text
Beginner
Agents
Tools
Workflows
Human Interaction
Integrations
Runtime
Production
```

Each example should be executable.

---

## Recipes

Short goal-oriented solutions.

Examples:

* Add a tool
* Reuse an existing OpenAI agent
* Run with a session
* Pause and resume
* Answer human input
* Stream run events
* Add Langfuse
* Serve a Deck

---

## Migration Guides

Only migration-specific historical information belongs here.

---

# Part I  -  Homepage and site experience

# 47. Homepage Purpose

The homepage is not a table of contents.

It is also not a traditional marketing landing page.

Its job is:

> Explain AgentDeck, demonstrate simplicity, and route developers into the correct path.

The developer should encounter real code almost immediately.

---

# 48. Homepage Structure

## Hero

Include:

* AgentDeck logo/wordmark,
* one strong product sentence,
* one supporting sentence,
* primary CTA,
* GitHub CTA,
* short code example.

Suggested positioning direction:

> **Build and run agentic systems from composable parts.**

Supporting idea:

> Compose agents, tools, workflows and skills in one runtime  -  without giving up the frameworks you already use.

This copy may evolve independently from this specification.

---

## Mental Model

Immediately show the core composition diagram.

---

## Three Product Ideas

Limit this section to three strong ideas.

### Compose

Agents, tools, workflows and skills become one system.

### Bring Your Stack

Reuse existing agents and integrations.

### Operate

Get lifecycle, control, persistence and observability through one runtime model.

---

## Choose Your Path

Cards:

* Build your first agent
* Bring an existing agent
* Build a workflow
* Run AgentDeck in production

---

## Integrations

Show only real supported integrations.

No roadmap logo wall.

---

# Part II  -  Brand and visual design

# 49. Visual Goal

AgentDeck documentation should feel like a carefully engineered developer product.

It should not look like:

* default Nextra,
* default Docusaurus,
* generic SaaS,
* generic AI startup,
* documentation with a logo pasted on top.

The visual identity should be recognizable as AgentDeck even when the wordmark is not visible.

---

# 50. Brand Personality

The visual identity should communicate:

```text
technical
precise
modern
fast
controlled
confident
engineered
```

It should not communicate:

```text
corporate
playful AI toy
crypto
gaming
cyberpunk
generic SaaS
```

---

# 51. Brand Geometry

The AgentDeck geometric cue  -  including the cut/diamond/card-inspired shape from the approved identity  -  may influence:

* selected navigation,
* callouts,
* cards,
* small diagram nodes,
* icon treatment,
* section accents.

It should be subtle.

The site should not become a literal deck-of-cards interface.

---

# 52. Color

Use the approved AgentDeck brand palette as a design-token source.

The content area should remain primarily neutral.

Brand colors should be concentrated in:

* logo,
* links,
* active navigation,
* CTAs,
* diagrams,
* small highlights,
* important interactive states.

Avoid large saturated backgrounds across ordinary documentation pages.

Readability wins over brand saturation.

---

# 53. Typography

Typography should strongly support technical reading.

Requirements:

* excellent body readability,
* strong heading hierarchy,
* high-quality monospace,
* comfortable line height,
* moderate content width,
* clear visual distinction between prose and code.

The wordmark font does not need to become the body font.

Brand consistency should not reduce readability.

---

# 54. Layout

Documentation should feel spacious.

Prefer:

```text
clear hierarchy
generous whitespace
short readable line length
large spacing between major sections
stable sidebar
predictable right-side table of contents
```

Avoid visually dense walls of text.

---

# 55. Code as a Visual Asset

Code blocks are central design elements.

They should support:

* syntax highlighting,
* copy action,
* optional filename,
* highlighted lines,
* line numbers only when useful,
* tabs when genuinely necessary,
* output blocks,
* language labeling.

Do not overdecorate code.

---

# 56. Component System

Maintain a deliberately small documentation component library.

Core components:

* CodeBlock
* Callout
* Steps
* Tabs
* Cards
* API Signature
* Badge
* Diagram
* FileTree
* Comparison

Avoid adding components without a recurring documentation need.

The documentation design system should obey the same complexity budget as the SDK.

---

# 57. Callouts

Use a small semantic set:

### Note

Useful additional information.

### Important

Behavior that materially affects correct usage.

### Experimental

Capability may change.

### Warning

Potentially destructive or surprising behavior.

Do not invent many visually similar callout types.

---

# 58. Navigation Design

Sidebar hierarchy should generally remain within two levels.

Avoid deep trees.

A developer should be able to visually understand the documentation model without expanding ten folders.

---

# 59. Top Navigation

Recommended shape:

```text
Docs
Examples
Integrations
API
Changelog

Search

GitHub
```

The exact labels may change, but the navigation should remain small.

---

# 60. Search

Search is a core documentation feature.

It must index:

* page titles,
* headings,
* prose,
* API symbols,
* configuration keys,
* event names.

Queries such as:

```text
ctx.invoke
Run.cancel
WAITING_ANSWER
session_id
```

should produce useful exact results.

API/reference matches should rank strongly for symbol searches.

---

# 61. Mobile

Mobile documentation must remain genuinely usable.

Requirements:

* navigation accessible without layout breakage,
* code blocks horizontally manageable,
* copy button reachable,
* headings and callouts readable,
* no critical information dependent on hover,
* diagrams responsive or scrollable.

Mobile should not be treated as an afterthought.

---

# 62. Accessibility

Target modern accessibility expectations.

At minimum:

* semantic HTML,
* keyboard navigation,
* visible focus states,
* sufficient contrast,
* alt text for meaningful diagrams,
* no color-only meaning,
* reduced-motion support,
* accessible search/navigation.

---

# Part III  -  Discoverability and machine consumption

# 63. Search Engine Structure

Every page should have:

* unique title,
* concise description,
* canonical URL,
* meaningful heading structure,
* stable URLs where possible,
* internal links to related pages.

Avoid keyword stuffing.

Clarity is the SEO strategy.

---

# 64. LLM-Friendly Documentation

AgentDeck documentation should be easy for language models to understand and cite.

Prefer explicit definitions:

> `Deck` is AgentDeck's top-level composition object.

Over vague marketing prose.

Important concepts should have canonical pages.

Avoid spreading the authoritative definition of one concept across many locations.

---

# 65. Machine-Readable Surfaces

Maintain:

```text
sitemap.xml
llms.txt
llms-full.txt
clean Markdown/MDX sources
stable canonical URLs
GitHub source links
```

These complement good documentation.

They do not replace it.

---

# Part IV  -  Success metrics

# 73. New Developer Test

Give the site to someone unfamiliar with AgentDeck.

Within 30 seconds they should answer:

> What is AgentDeck?

Within five minutes:

> Run a basic agent.

Within approximately fifteen minutes:

> Add a tool or workflow.

---

# 74. Existing Framework Test

Give the site to someone using a supported framework.

They should find within approximately one minute:

> How do I bring my existing agent into AgentDeck?

---

# 75. Lookup Test

Ask:

> What does `ctx.invoke()` do?

Or:

> How do I cancel a run?

Or:

> What is the current state of a Run?

The answer should be reachable within seconds.

---

# 76. Navigation Test

A user should rarely need to understand the repository structure to find documentation.

The public information architecture should make sense independently.

---

# 77. Content Quality Test

A successful page should produce one of two outcomes:

```text
"I understand this."
```

or:

```text
"I know exactly what to do next."
```

If it primarily communicates how much machinery AgentDeck contains, it has probably failed.

---

# Part V  -  Migration

# 78. Do Not Incrementally Polish the Current Information Architecture

Docs v2 should be treated as a structural rebuild.

Do not preserve a weak page merely because it already exists.

Reuse accurate material selectively.

Do not preserve its organization by default.

---

# 79. Content Migration Process

For every current page, classify it:

```text
KEEP
REWRITE
SPLIT
MOVE TO REFERENCE
MOVE TO DESIGN DOCS
MOVE TO MIGRATION
DELETE
```

Most existing prose should not be copied automatically.

---

# 80. Recommended Implementation Order

## Phase 1  -  Foundation

* final IA,
* navigation,
* design tokens,
* typography,
* core components,
* search architecture.

## Phase 2  -  First Experience

* homepage,
* overview,
* quickstart,
* mental model,
* agents,
* tools,
* Deck,
* Runs.

## Phase 3  -  Core Product

* workflows,
* skills,
* context,
* invocation,
* human interaction,
* events,
* sessions.

## Phase 4  -  Interoperability

* existing agents,
* OpenAI Agents,
* LangGraph,
* MCP,
* other supported integrations.

## Phase 5  -  Production

* persistence,
* serving,
* observability,
* deployment,
* troubleshooting.

## Phase 6  -  Reference & Automation

* generated API reference,
* configuration reference,
* event reference,
* docs CI,
* canonical example testing.

## Phase 7  -  Cleanup

* redirects,
* remove obsolete pages,
* archive historical docs,
* validate search,
* validate mobile,
* validate machine-readable outputs.

---

# Part VI  -  Definition of done

Docs v2 is ready when:

* a new user understands AgentDeck immediately,
* the Quickstart works from a clean environment,
* primary examples are tested in CI,
* concepts and reference are clearly separated,
* existing-agent integration is first-class,
* no primary page acts as an internal architecture journal,
* search works well for API symbols and concepts,
* the site has a distinct AgentDeck visual identity,
* light and dark modes are polished,
* mobile usage is functional,
* maturity status is visible where necessary,
* obsolete documentation has been removed or redirected,
* API reference is derived from authoritative sources where practical,
* `llms.txt`, `llms-full.txt`, sitemap, metadata, and canonical URLs are correct,
* the public documentation represents the current recommended way to use AgentDeck.

---

# Final Principle

Everything in this specification follows the same principle as AgentDeck itself:

> **The user should experience clarity because we did the difficult thinking beforehand.**

The SDK should not expose complexity simply because the runtime is sophisticated.

The documentation should not expose complexity simply because the architecture is sophisticated.

The visual design should not become complicated simply because the product is powerful.

The internal implementation should not become complicated simply because the problem is difficult.

AgentDeck should continuously search for the smallest, clearest, strongest model that correctly solves the problem.

That is the standard:

> **Power without burden.
> Depth without clutter.
> Sophistication without unnecessary complexity.
> Simple outside. Elegant inside.**
