# AgentDeck Engineering Principles & Documentation Specification

**Status:** Proposed
**Scope:** AgentDeck product philosophy, developer experience, documentation architecture, content system, visual identity, and documentation operations
**Primary goal:** Define the principles and system required to make AgentDeck feel exceptionally simple, coherent, trustworthy, and pleasant to use despite the sophistication of the runtime beneath it.

---

# Part I  -  How We Build AgentDeck

## 1. The Product Philosophy

AgentDeck should be built around a simple belief:

> **Great software does the hard work so its users do not have to.**

Complexity does not disappear simply because an API looks simple. Someone must understand it, model it, resolve its edge cases, and decide how the system should behave.

That responsibility belongs primarily to us.

The user should not be required to understand the internal machinery of AgentDeck in order to benefit from it.

At the same time, we must never confuse sophistication with complexity.

A large implementation is not automatically a better implementation. More layers are not automatically better architecture. More abstractions are not automatically more reusable.

Our goal is:

> **Simple on the outside. Elegant on the inside.**

Not:

> Simple on the outside. Unmaintainable underneath.

We should spend our effort on understanding the problem deeply enough that the resulting model becomes smaller, clearer, and more coherent.

This philosophy should influence every API, abstraction, runtime behavior, error message, integration, example, and documentation page we create.

---

# 2. Simplicity Is Earned



> “Simple can be harder than complex: you have to work hard to get your thinking clean to make it simple.”
>  -  Steve Jobs, BusinessWeek, 1998

Simple software is rarely produced by avoiding difficult thinking.

Usually the opposite is true.

The first solution to a difficult problem often contains:

* many special cases,
* excessive configuration,
* duplicated state,
* several abstractions,
* multiple competing concepts,
* implementation details exposed through the public API.

The better solution often appears only after the problem has been understood more deeply.

The engineering process should therefore look like:

```text
Complex problem
      ↓
Understand the real constraints
      ↓
Identify the invariants
      ↓
Find the smallest useful model
      ↓
Design strong primitives
      ↓
Hide unnecessary machinery
      ↓
Expose a simple API
```

We should not optimize for how quickly we can produce the first architecture.

We should optimize for reaching the **right architecture**.

---

# 3. Do the Hard Thinking, Not Unnecessary Hard Engineering

AgentDeck may legitimately require difficult engineering.

Durable runs, lifecycle management, concurrency, resumability, events, persistence, control signals, interoperability, context propagation, observability, and distributed execution are difficult problems.

We should solve them properly.

But complexity must always justify itself.

Before introducing another abstraction, state machine, interface, service, registry, adapter, configuration object, or protocol, ask:

1. What concrete problem does this solve?
2. Can an existing primitive solve it?
3. Does this abstraction remove complexity or merely move it?
4. Will a future maintainer understand why it exists?
5. Does the user gain meaningful capability from the additional complexity?
6. Can the same behavior be expressed with a smaller model?

The objective is not minimum code at all costs.

The objective is:

> **The smallest coherent system that correctly solves the problem.**

---

# 4. Complexity Has a Budget

Every feature spends complexity.

Complexity appears as:

* concepts a user must learn,
* parameters they must configure,
* states they must reason about,
* objects they must manage,
* internal mechanisms maintainers must understand,
* interactions between subsystems,
* failure modes,
* documentation burden,
* testing burden.

Complexity should therefore be treated as a budget.

A feature should earn the complexity it introduces.

A useful design question is:

> **What capability are we buying with this complexity?**

If the answer is weak, the design should probably become smaller.

---

# 5. The User Should Own Intent, AgentDeck Should Own Machinery

Users should primarily describe:

* what agents exist,
* what tools they can use,
* what workflows exist,
* what skills exist,
* how they compose,
* what context their application provides,
* what they want to execute.

AgentDeck should own as much runtime machinery as it can reliably own:

* run identity,
* lifecycle state,
* event recording,
* execution control,
* persistence integration,
* suspension and resumption,
* cancellation,
* concurrency protection,
* context propagation,
* observability hooks,
* cleanup,
* runtime bookkeeping,
* supported framework differences.

The public API should represent **user intent**, not implementation mechanics.

---

# 6. Strong Primitives Over Many Features

We should prefer a small number of primitives that compose well over many overlapping APIs.

Examples of core primitives include:

* `Agent`
* `Tool`
* `Workflow`
* `Skill`
* `Deck`
* `Run`
* `Context`
* `Event`

A primitive is good when many capabilities emerge naturally from it.

For example, run management should not require separate unrelated APIs for:

* starting,
* finding,
* pausing,
* resuming,
* answering,
* cancelling,
* observing.

A coherent model such as:

```python
run = await deck.runs.start(...)

await run.pause()
await run.resume()
await run.answer(...)
await run.cancel()

async for event in run.events():
    ...

result = await run
```

is preferable because the object model matches how developers naturally think about the problem.

---

# 7. Abstractions Must Delete Complexity

An abstraction is successful when it allows the caller to know less.

It is unsuccessful when it merely hides code behind another name while preserving all of the same cognitive burden.

Before creating an abstraction, ask:

> **What no longer needs to be understood after this abstraction exists?**

If the answer is "nothing," the abstraction may not be useful.

---

# 8. One Obvious Path First

For common operations, AgentDeck should have one obvious recommended path.

Advanced alternatives may exist, but they should not compete with the happy path.

Prefer:

```python
await deck.run(...)
```

over requiring the user to choose between several equivalent orchestration APIs before they understand the system.

The product should teach:

> **Here is the normal way.**

Then:

> Here are the escape hatches when you need them.

---

# 9. Good Defaults Are Product Design

Configuration is not free.

Every required configuration option represents a decision we have transferred from AgentDeck to the user.

If AgentDeck can make a safe, sensible decision for most users, it should.

Defaults should make:

```text
install
→ define
→ run
```

possible with very little setup.

Configuration should primarily exist when:

* the correct answer is genuinely application-specific,
* the user is overriding a meaningful policy,
* production environments require explicit behavior.

---

# 10. Escape Hatches Without Abstraction Prison

AgentDeck should simplify other systems without trapping users behind AgentDeck.

If a developer already has:

* OpenAI Agents,
* LangGraph,
* another supported agent runtime,
* existing tools,
* existing MCP servers,
* existing application infrastructure,

AgentDeck should prefer wrapping, adapting, and composing rather than forcing a rewrite.

When possible:

> **Use the AgentDeck abstraction for the common path. Keep access to the native object for the uncommon path.**

AgentDeck should reduce switching costs, not create new ones.

---

# 11. Interoperability Is a Core Product Property

AgentDeck is not valuable because it creates yet another isolated agent ecosystem.

Its value increases when developers can combine existing work.

We should therefore prefer architecture that allows:

```text
existing agent
existing workflow
existing tool
existing skill
existing protocol
        ↓
     AgentDeck
```

rather than:

```text
rewrite everything
        ↓
     AgentDeck
```

---

# 12. Correctness Before Convenience, Convenience After Correctness

A convenient API that behaves unpredictably is not simple.

It is dangerous.

We should first establish:

* clear invariants,
* deterministic lifecycle rules,
* explicit ownership,
* race-safe operations,
* reliable persistence semantics,
* understandable failures.

Then hide those mechanics behind a convenient surface.

The ideal result is:

> **Strict internally, forgiving externally.**

---

# 13. Make Invalid States Difficult to Express

Whenever possible, architecture should prevent invalid combinations rather than document them.

Prefer:

* validation during build,
* typed state,
* coherent object ownership,
* explicit lifecycle boundaries,

over discovering invalid configuration halfway through a run.

Errors should happen as early as the system can confidently identify them.

---

# 14. Errors Are Part of the API

An error should tell the developer:

1. what happened,
2. why it happened,
3. what they can do next.

Bad:

```text
Invalid state transition.
```

Better:

```text
Run 'abc' is waiting for an answer and cannot be resumed.
Use `await run.answer(value)` instead.
```

A developer should not need to inspect AgentDeck source code to recover from ordinary mistakes.

---

# 15. Public APIs Should Age Slowly

Internal implementations may evolve aggressively.

Public concepts should evolve carefully.

Before adding a public concept, consider whether it deserves to exist for years.

Prefer changing internals instead of expanding the external conceptual model.

A smaller public surface gives AgentDeck more freedom to improve internally.

---

# 16. Readability Is an Engineering Requirement

Code should optimize for the next person reading it.

Prefer:

* explicit invariants,
* meaningful names,
* localized policy,
* small coherent units,
* predictable ownership,
* comments explaining *why*,

over:

* clever tricks,
* excessive indirection,
* framework-like machinery without clear benefit.

Sophisticated code can still be readable.

---

# 17. Design Before Implementation

For meaningful runtime behavior, the preferred order is:

```text
Problem
→ user story
→ invariants
→ lifecycle/state model
→ public API
→ edge cases
→ implementation
→ tests
```

Not:

```text
implementation
→ discover behavior accidentally
→ document whatever happened
```

Thinking is part of implementation.

---

# 18. Tests Protect the Model, Not Just the Code

Important tests should verify product guarantees.

Examples:

* one run owns its identity,
* terminal means terminal,
* cancellation eventually becomes durable,
* two callers cannot both win an atomic transition,
* stopping event consumption does not accidentally redefine lifecycle semantics,
* a recovered run behaves like the same run,
* adapters preserve AgentDeck's runtime contract.

Tests should preserve **meaning**, not implementation accidents.

---

# 19. Delete Aggressively

Removing complexity is a feature.

We should regularly ask:

* Can this object disappear?
* Can these two concepts become one?
* Can configuration become a default?
* Can this special case become a general rule?
* Can this layer be removed?
* Can this internal mechanism remain private?
* Can this documentation page disappear?

The project should become conceptually cleaner as it becomes more capable.

---

# 20. The AgentDeck Engineering North Star

> “You've got to start with the customer experience and work backwards to the technology.”
>  -  Steve Jobs, WWDC 1997

The engineering north star is:

> **Make powerful agentic systems feel obvious to build, compose, run, and operate.**

Supporting principles:

> **Simplicity is earned through understanding.**

> **Do the hard thinking, not unnecessary hard engineering.**

> **Every piece of complexity AgentDeck can reliably own is one less piece its users should have to own.**

> **The size of the implementation is not a reason for the public API to grow.**

> **Simple outside. Elegant inside.**

These principles apply equally to code, architecture, APIs, documentation, integrations, CLI design, errors, configuration, and developer experience.

---

# Part II  -  Documentation

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

# 25. Documentation Scope Boundary

The public docs describe:

> **How users should use AgentDeck today.**

They do not exist to record every decision made while creating AgentDeck.

Separate:

```text
docs-site/
    user-facing documentation

docs/design/
    architecture

docs/rfcs/
    proposals

docs/delivery/
    milestones and implementation planning

GitHub issues/
    active discussion
```

The public docs must not become a development diary.

---

# 26. Documentation Principles

## 26.1 Answer First

Start with the useful answer.

Bad:

> AgentDeck's runtime abstraction evolved in response to...

Good:

> A `Run` represents one execution of an AgentDeck invocable.

---

## 26.2 Code Before Deep Theory

Show the normal operation quickly.

Explain architecture after the reader understands what the feature does.

---

## 26.3 Progressive Disclosure

Information order should generally be:

```text
What
↓
Why
↓
Minimal usage
↓
Common behavior
↓
Advanced behavior
↓
Reference
↓
Internals, if truly necessary
```

---

## 26.4 One Page, One Main Idea

Avoid pages that simultaneously teach:

* runs,
* sessions,
* event sourcing,
* persistence,
* schema evolution,
* concurrency.

Split them.

---

## 26.5 Current Truth Only

Normal documentation explains the current recommended API.

Historical behavior belongs in:

* migration guides,
* changelog,
* release notes.

Do not pollute normal concepts with:

> "Before v3 this was called..."

---

## 26.6 Teach the Recommended Path

Every capability should have a preferred pattern.

Alternative approaches should appear later.

---

## 26.7 Do Not Expose Internals Without User Value

A user learning workflows does not initially need:

* timer sweep implementation,
* checkpoint internals,
* event sequence allocation,
* lease renewal strategy.

These details may be important elsewhere.

They are not introductory workflow documentation.

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

# 37. Page Grammar

Consistency is mandatory.

Every page type has a grammar.

---

## Concept Page

```text
Title

One-sentence definition

When to use it

Minimal example

How it works

Common patterns

Important behavior

Related concepts

Next step
```

---

## Guide

```text
Goal

Prerequisites

Implementation

Run it

Expected result

How it works

Next steps
```

---

## Integration Page

```text
What it gives you

Install

Minimal integration

AgentDeck behavior

Native access

Supported capabilities

Limitations

Next steps
```

---

## Reference Page

```text
Signature

Parameters

Returns

Exceptions

Behavior

Example

Related APIs
```

---

# 38. Writing Style

The AgentDeck documentation voice should be:

* confident,
* technical,
* concise,
* practical,
* calm,
* precise.

Avoid marketing language such as:

* revolutionary,
* cutting-edge,
* incredibly powerful,
* seamless,
* next-generation,

unless a concrete statement immediately substantiates it.

Prefer:

> Use a workflow when execution has explicit steps or state.

Over:

> AgentDeck's powerful workflow engine unlocks advanced orchestration possibilities.

---

# 39. Paragraph and Sentence Rules

Prefer short paragraphs.

Most paragraphs should contain one to three sentences.

Headings should communicate information rather than decorate prose.

Lists should be used when structure improves scanning.

Do not turn every sentence into a bullet.

---

# 40. Code Rules

Code is a primary communication medium.

Examples should:

* be short,
* be complete enough to understand,
* use the recommended API,
* avoid unrelated setup,
* avoid hypothetical APIs,
* be executable whenever practical.

A page should not present five equivalent approaches unless comparison is the purpose of the page.

Label alternatives explicitly.

---

# 41. Canonical Examples

Maintain a small set of canonical examples that represent the product.

Minimum set:

```text
basic-agent
tool
workflow
skill
handoff
human-input
run-control
existing-agent
mcp
persistence
observability
```

These examples should be reused across:

* docs,
* tests,
* README,
* marketing snippets where appropriate.

Do not maintain slightly different versions of the same basic example everywhere.

---

# 42. Documentation for a Fast-Moving SDK

AgentDeck is evolving quickly.

The documentation system must expect this.

Do not wait for the SDK to become "finished."

Instead separate stable conceptual documentation from moving API details.

---

# 43. Maturity Levels

Public capabilities may carry:

### Stable

Expected to preserve compatibility.

### Beta

Usable and supported, but API may evolve.

### Experimental

Actively being designed and expected to change.

Internal implementation should not appear as a public maturity level because it should not appear as public documentation.

Badges should be visible but not visually overwhelming.

---

# 44. Version Strategy

During rapid development, maintain primarily:

```text
Latest
```

Avoid maintaining many historical documentation versions unless users actually depend on them.

Once major stable lines require preservation, versioning may become:

```text
Latest
v5
v4
```

Old versions should not pollute normal search results by default.

---

# 45. Documentation Changes Are Part of API Changes

A public API change is incomplete until the affected documentation changes.

Feature PRs should update:

* relevant concept page,
* affected guide/example,
* API reference source/docstring,
* migration note when breaking.

Documentation should not be a cleanup task after release.

---

# 46. Documentation CI

Important code examples must be protected.

Where technically practical:

* extract examples,
* type-check them,
* execute them,
* test canonical examples,
* validate internal links,
* detect missing pages,
* validate reference generation.

Breaking a canonical example should break CI.

This makes documentation a runtime compatibility surface rather than static prose.

---

# Part III  -  Homepage & Site Experience

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

# Part IV  -  Brand & Visual Design

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

# Part V  -  Discoverability & Machine Consumption

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

# Part VI  -  What Must Not Appear in Primary Docs

# 66. Excluded Content

The normal documentation navigation should not contain:

* internal architecture debates,
* rejected proposals,
* delivery plans,
* milestone tracking,
* raw issue dumps,
* huge roadmap documents,
* implementation diaries,
* old experimental APIs,
* ADR-level reasoning,
* contributor-only details.

These may remain public in the repository, but they are not product documentation.

---

# 67. Known Issues

Known issues should not dominate primary navigation.

Prefer:

* troubleshooting pages,
* GitHub issues,
* concise release-specific notices.

Only high-impact user-facing limitations deserve prominent documentation.

---

# 68. Roadmap

The roadmap is not documentation for current functionality.

If surfaced, it should be clearly separated from current product behavior.

Never make a future feature visually indistinguishable from an available feature.

---

# Part VII  -  Documentation Operations

# 69. Ownership

Documentation has three forms of ownership.

### Concept ownership

Maintainers responsible for the underlying capability.

### Editorial ownership

Someone responsible for clarity, structure, tone, and consistency.

### Docs engineering ownership

Someone responsible for:

* site infrastructure,
* search,
* code validation,
* reference generation,
* redirects,
* deployment.

One person may fill multiple roles, but the responsibilities must exist.

---

# 70. Source of Truth

Prefer:

```text
conceptual behavior
→ manual documentation

API shape
→ source code / docstrings

examples
→ executable canonical examples

history
→ changelog / migration guides

internal decisions
→ ADR/RFC/design docs
```

Do not manually duplicate information when another authoritative source can generate it.

---

# 71. Review Checklist for Every New Page

Before merging, ask:

### User

* Who is this page for?
* What question does it answer?
* Does the answer appear quickly?

### Content

* Is there unnecessary history?
* Is there unnecessary implementation detail?
* Can prose be deleted?
* Is there a minimal example?

### Architecture

* Is this information already documented elsewhere?
* Does this page introduce a new concept unnecessarily?

### Code

* Does the example represent the recommended API?
* Does it run?

### Navigation

* Is this the right location?
* Does this page deserve to exist?

---

# 72. The Delete Test

Before publishing a page, ask:

> If this page disappeared, what user task would become harder?

If the answer is unclear, the page may not be needed.

---

# Part VIII  -  Success Metrics

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

# Part IX  -  Docs v2 Migration

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

# Part X  -  Definition of Done

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
