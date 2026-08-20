# Keeping AI Coding Agents Aligned With the Repository

**Research synthesis and proposed engineering solution**
**Date:** 2026-08-20
**Scope:** AI coding agents working autonomously or semi-autonomously in existing software repositories.

---

## Executive summary

Modern coding agents can produce functionally correct code while still making a repository worse as a software system.

The recurring failure mode is not simply "bad code." It is **local optimization without repository-level engineering discipline**:

- unnecessary lines of code,
- narrative comments that restate the implementation,
- duplicated helpers instead of reuse,
- new abstractions without architectural justification,
- multiple patterns for the same concept,
- gradual dependency drift,
- code that passes tests but does not look as if it belongs in the repository,
- agents that begin implementing before they understand the existing design.

The evidence reviewed here suggests that this is a real and increasingly recognized problem. OpenAI reported that in a million-line, agent-generated internal product, a large monolithic `AGENTS.md` did not work well, documentation alone was insufficient, and humans initially spent roughly 20% of their week cleaning up "AI slop." Their response was not a better prompt. They moved repository knowledge into versioned documentation, mechanically enforced architectural constraints, encoded "golden principles," and added recurring cleanup agents.[1]

Independent research points in the same direction. A 2026 empirical study reported that developers using AI added comments more frequently than non-AI developers (79% versus 54%), noting the risk of noisy surface-level comments.[2] Another 2026 study using detector-based proxy analysis found substantial intra-repository cloning in code detected as likely LLM-generated.[3] A separate 2026 preprint reports a relationship between increased AI-generated code volume and structural degradation, arguing that functional correctness and detailed prompting alone do not solve architectural decay.[4]

The solution proposed in this document is therefore:

> **Treat the repository as an executable engineering contract, not as a bag of files plus an agent prompt.**

The repository should contain the knowledge, constraints, workflows, verification surfaces, and feedback loops that make a good engineering decision the easiest decision for any coding agent.

The complete system has eight parts:

1. **A small agent entry point** — a map, not an encyclopedia.
2. **Versioned architectural knowledge and invariants** — one source of truth.
3. **A machine-readable repository map** — symbols, dependencies, ownership, and relevant history.
4. **An inspect → design → implement workflow** for non-trivial changes.
5. **A mandatory reuse and architecture analysis** before introducing new abstractions.
6. **Deterministic enforcement** for anything a machine can prove.
7. **Semantic review** only for qualities that require judgment.
8. **Continuous repository garbage collection** so entropy is removed before it compounds.

This is not tied to Codex, Claude Code, Cursor, Copilot, Kiro, or any other agent. Those tools should be treated as execution engines operating against the same repository contract.

---

# 1. The problem

## 1.1 The code can be correct and still smell wrong

The difficult case is not an agent producing code that does not compile.

Tests, compilers, type systems, and conventional linters catch a large portion of those failures.

The harder case looks like this:

```python
# Increment the retry count
retry_count += 1

# Check if the retry count exceeds the maximum
if retry_count > max_retries:
    # Raise an error because retries have been exhausted
    raise RetryLimitExceeded()
```

There may be nothing functionally incorrect here.

But a mature codebase may already communicate this behavior clearly through names and decomposition. The comments add maintenance cost without adding knowledge.

A more serious version is:

```python
class CancellationManager:
    ...
```

added to implement cancellation even though the repository already has:

```python
class RunController:
    def cancel(...):
        ...
```

Again, the new class may work.

The repository is still worse because it now has two concepts for the same responsibility.

This is the core distinction:

> **Functional correctness is local. Software coherence is global and historical.**

A coding model often sees enough information to solve the immediate task but not enough engineering intent to understand why the repository has the shape it has.

---

## 1.2 The agent optimizes the current change, not the lifetime of the repository

A human engineer working in a codebase for months accumulates knowledge that is rarely written in one place:

- why a class exists,
- why a dependency was rejected,
- which abstraction is canonical,
- which old pattern is being removed,
- where persistence logic belongs,
- when a helper should be shared,
- which apparently strange implementation protects an invariant,
- which comments are useful and which are noise.

An agent typically enters with a fresh context window.

Without a repository-level system, it must reconstruct that knowledge from:

- source files,
- arbitrary documentation,
- search,
- the current prompt,
- and patterns it happens to encounter first.

That is an unreliable control system.

---

# 2. Evidence that the problem is real

This section separates **observed evidence** from the recommendations later in the document.

## 2.1 OpenAI: a million-line agent-generated repository

In February 2026, OpenAI published a detailed account of an internal software product built with no manually written code. The repository grew to roughly one million lines and about 1,500 pull requests in five months.[1]

Several findings are directly relevant.

### A giant `AGENTS.md` failed

OpenAI reports that they initially tried putting large amounts of repository guidance into one instruction file.

They moved away from this because:

- context is limited,
- too many rules dilute attention,
- the document becomes stale,
- and a monolithic document is difficult to validate mechanically.[1]

Their replacement was a short `AGENTS.md` serving primarily as a map into a structured repository knowledge base including architecture documents, design documents, execution plans, product specifications, quality information, reliability information, and security documentation.[1]

This is evidence against the common strategy:

```text
Make the agent better
=
write a longer system prompt
```

### Documentation was not enough

OpenAI explicitly reports that architecture and engineering taste also needed mechanical enforcement.

Their codebase uses a fixed architectural model with validated dependency directions and a restricted set of legal edges. Custom linters and structural tests enforce those rules.[1]

That is a critical distinction:

```text
"Please follow our architecture"
```

is guidance.

```text
domain -> infrastructure = CI failure
```

is enforcement.

### They experienced repository entropy directly

OpenAI reports that their engineers initially spent every Friday — approximately 20% of the work week — cleaning up agent-generated code quality problems.[1]

Their response was to encode "golden principles" as repository rules and run recurring Codex tasks that:

- scan for deviations,
- update quality grades,
- open targeted refactoring PRs,
- and continuously remove technical debt.[1]

They describe this as analogous to garbage collection.

The production lesson is important:

> Do not assume each generation will be perfect. Build a restoring force that continually moves the repository back toward its intended shape.

---

## 2.2 AI-assisted developers add more comments

A 2026 study published in *Empirical Software Engineering* examined downstream maintainability effects of AI assistants.

In one detailed task analysis, AI-assisted developers added comments in **79%** of submissions compared with **54%** for developers without AI assistance.[2]

The authors specifically discuss noisy, surface-level comments as a maintainability concern.

This matches a common observable pattern:

```python
# Create a new list
results = []

# Iterate through each item
for item in items:
    # Add the transformed item to the list
    results.append(transform(item))
```

The issue is not "comments are bad."

The issue is that agents often optimize for *explicitness of generated text* rather than *information density for future maintainers*.

---

## 2.3 Evidence of intra-repository cloning

A 2026 *Journal of Systems and Software* study analyzed code detected as likely LLM-generated across active repositories from 2021–2025 using detector-based proxy analysis.[3]

Among its findings, code detected as likely LLM-generated showed substantial intra-repository code clones, with company-maintained repositories showing a higher proportion of such clones in likely LLM-generated code.[3]

This does not prove that every detected block was generated by an LLM; the authors are explicit that detector-based analysis is a proxy.

It nevertheless supports a practical engineering concern:

> Agents can implement behavior that already exists because finding an existing abstraction is harder than generating a new local solution.

---

## 2.4 Architectural degradation and code volume

A 2026 preprint, *AI-Generated Smells: An Analysis of Code and Architecture in LLM and Agent-Driven Development*, analyzes maintainability across both small generated programs and more complex agent-generated systems.[4]

The authors report what they call a **Reasoning-Complexity Trade-off**: more capable models can produce increasingly bloated and coupled code, and code volume strongly correlates with structural degradation in their experiments.[4]

The paper is a preprint and its broad conclusions should be treated accordingly, but it is useful because it focuses on a dimension that conventional coding benchmarks usually under-measure:

```text
Does it work?
```

is not the same question as:

```text
Did the repository become easier or harder to evolve?
```

---

## 2.5 Agent PRs behave differently from human PRs

A 2026 study of GitHub pull requests analyzed:

- 24,014 merged agentic PRs,
- 440,295 commits in those PRs,
- 5,081 merged human PRs,
- 23,242 commits in those PRs.[5]

The authors found substantial differences in commit count and moderate differences in files touched and deleted lines.[5]

This does not directly establish maintainability problems, but it demonstrates that agentic contribution patterns are measurably different at scale and therefore justify agent-specific engineering controls rather than assuming conventional human workflows are sufficient.

---

## 2.6 Multi-agent concurrency increases coordination risk

A July 2026 study analyzed 33,596 AI-agent PRs across 2,807 repositories.[6]

For replayed co-active PR pairs, textual merge conflicts occurred more frequently in cross-agent pairs than intra-agent pairs: **41.7% versus 19.8%**.[6]

The authors note that textual conflicts are only a lower bound because semantic conflicts can exist even when Git merges cleanly.

This matters if autonomous agents increasingly work in parallel.

Repository alignment is not only:

```text
agent <-> codebase
```

It is also:

```text
agent A <-> shared architecture <-> agent B
```

---

# 3. What the evidence suggests

The evidence does **not** imply:

- AI should not write code,
- large changes are always bad,
- comments should be removed,
- agents cannot refactor,
- humans are automatically more maintainable.

It suggests something more useful:

> **The engineering environment around the agent matters as much as, and sometimes more than, the model prompt.**

This is also the conclusion reflected in several current tools and workflows:

- OpenAI's repository harness approach,[1]
- Adobe's repository harness guide,[7]
- GitHub Spec Kit's constitution/spec/plan/task flow,[8]
- Claude Code's explore → plan → implement guidance and hooks,[9][10]
- Kiro's requirements → design → tasks → execution specs,[11]
- Aider's repository map and Architect/Editor separation,[12][13]
- architecture-contract tools such as Import Linter,[14]
- semantic review systems such as CodeRabbit and Qodo.[15][16]

No single one solves the entire problem.

Together they reveal a coherent architecture.

---

# 4. Why prompt-only solutions are insufficient

Suppose the root `AGENTS.md` contains:

```markdown
- Reuse existing abstractions.
- Do not over-engineer.
- Keep code concise.
- Follow the existing architecture.
- Avoid unnecessary comments.
```

These are good principles.

They are also under-specified.

## 4.1 "Reuse existing abstractions" requires discovery

The agent cannot reuse something it did not find.

The real problem is:

```text
How does the agent know the existing abstraction exists?
```

A prompt cannot answer that.

Repository discovery can.

---

## 4.2 "Follow the architecture" requires a formal architecture

If the architecture is implicit in current code, then every current mistake becomes a possible example.

An agent that pattern-matches the repository may copy:

- legacy code,
- accidental coupling,
- transitional architecture,
- one-off workarounds.

Architecture must therefore be represented separately from accidental implementation history.

---

## 4.3 "Do not over-engineer" is not measurable

A model cannot reliably know your threshold for an unnecessary abstraction.

A stronger system asks:

```text
What existing abstraction was considered?

Why is it insufficient?

What new public concepts are being introduced?

What is the expected deletion or simplification enabled by the new abstraction?
```

Now over-engineering becomes a reviewable engineering decision.

---

## 4.4 Instructions are advisory

Claude Code's own documentation makes a useful distinction: `CLAUDE.md` is context, while permissions and hooks are appropriate for guarantees and boundaries.[17]

If a rule must hold every time, relying exclusively on model obedience is the wrong mechanism.

---

# 5. Proposed solution: the Repository Engineering Harness

I will call the complete system a **Repository Engineering Harness**.

Adobe independently uses the term **repository harness** for repository-local control systems around coding agents.[7] The proposal below extends that idea specifically toward architectural coherence, reuse, code smell control, and long-term entropy management.

The objective is:

> Any competent coding agent entering the repository should be able to reconstruct the intended engineering model quickly, make changes through the repository's existing patterns, prove why new abstractions are necessary, and be mechanically prevented from violating hard constraints.

The harness has eight layers.

---

# 6. Layer 1 — A small agent entry point

The root agent file should be a router.

Not a book.

Example:

```markdown
# AGENTS.md

## Goal

Extend this repository using its existing architecture and abstractions.
Prefer reducing concepts over adding concepts.

## Required workflow

For non-trivial changes:

1. Read the relevant architecture documentation.
2. Inspect existing implementations and call sites.
3. Identify reuse candidates.
4. Write a change plan before editing.
5. Implement the smallest coherent change.
6. Run `make check`.
7. Review the final diff for unnecessary code, abstractions, and comments.

## Repository map

- Architecture: `docs/ARCHITECTURE.md`
- Hard invariants: `INVARIANTS.md`
- Engineering principles: `docs/ENGINEERING.md`
- Canonical patterns: `docs/patterns/`
- Active plans: `docs/plans/active/`
- Decisions: `docs/decisions/`
- Testing: `docs/TESTING.md`

## Escalation rules

A written design justification is required before:

- introducing a new architectural layer,
- introducing a new public abstraction,
- adding a runtime dependency,
- bypassing an existing repository/service boundary,
- creating a second implementation of an existing capability.
```

This follows the pattern OpenAI reports: a small stable entry point with progressive disclosure into deeper repository knowledge.[1]

Claude Code similarly recommends keeping always-loaded instructions concise and moving task- or path-specific guidance elsewhere.[9][17]

---

# 7. Layer 2 — Versioned engineering knowledge

A useful repository structure could be:

```text
repo/
│
├── AGENTS.md
├── INVARIANTS.md
├── README.md
├── Makefile
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ENGINEERING.md
│   ├── TESTING.md
│   │
│   ├── patterns/
│   │   ├── persistence.md
│   │   ├── errors.md
│   │   ├── lifecycle.md
│   │   └── concurrency.md
│   │
│   ├── decisions/
│   │   ├── 001-store-boundary.md
│   │   ├── 002-events-are-append-only.md
│   │   └── ...
│   │
│   └── plans/
│       ├── active/
│       └── completed/
│
├── .engineering/
│   ├── architecture.yaml
│   ├── quality.yaml
│   └── ownership.yaml
│
└── src/
```

The important rule is **single topic ownership**.

For example:

```text
Dependency direction
    -> owned by INVARIANTS.md / architecture.yaml

Runtime architecture
    -> owned by docs/ARCHITECTURE.md

Testing commands
    -> owned by docs/TESTING.md
```

Other documents link to those sources rather than copying them.

Adobe's repository harness guide recommends this kind of explicit ownership to reduce documentation drift.[18]

---

# 8. Layer 3 — Machine-readable repository understanding

Documentation explains intent.

The agent also needs a compact representation of the actual code.

Aider provides one useful precedent: its repository map extracts important symbols and relationships and supplies only the most relevant portions to the model.[12]

Aider uses Tree-sitter to identify definitions and references and ranks repository context through a dependency graph so the model can see important classes, functions, signatures, and relationships without loading the entire repository.[12]

A more complete repository model could contain:

```text
AST / symbol graph
+
module dependency graph
+
public API graph
+
test-to-code relationships
+
ownership
+
recent git changes
+
architectural metadata
+
semantic search index
```

Example generated view:

```text
Concept: Run cancellation

Relevant symbols:
  src/runtime/run.py
    Run.cancel(reason)
    Run.transition(state)

  src/runtime/controller.py
    RunController.cancel(run_id, reason)

  src/store/base.py
    RunStore.save(run)

Callers:
  api/runs.py -> RunController.cancel
  cli/runs.py -> RunController.cancel

Architecture:
  api -> application -> domain
  persistence adapters implement RunStore
  domain MUST NOT import persistence adapters

Recent history:
  2026-08-14: cancellation moved out of Session
  2026-08-17: CLI switched to RunController

Canonical pattern:
  docs/patterns/lifecycle.md
```

That context is far more useful than simply retrieving five semantically similar source chunks.

---

# 9. Layer 4 — Inspect before edit

For meaningful changes, the agent should be unable — procedurally or mechanically — to jump immediately to implementation.

Anthropic currently recommends:

```text
Explore
-> Plan
-> Implement
-> Verify
```

for complex changes and explicitly warns that jumping directly to coding can solve the wrong problem.[9]

Kiro formalizes a similar process as:

```text
Requirements
-> Design
-> Tasks
-> Execution
```

with persistent Markdown artifacts.[11]

GitHub Spec Kit uses an even more explicit flow:

```text
constitution
-> specify
-> clarify
-> plan
-> checklist
-> tasks
-> analyze
-> implement
-> converge
```

with optional gates depending on task complexity.[8]

The right implementation for a general repository should be adaptive.

## Change class A — trivial

Examples:

- typo,
- obvious variable rename,
- one-line bug,
- test fixture correction.

Workflow:

```text
inspect
-> edit
-> verify
```

No design artifact required.

## Change class B — normal feature or refactor

Workflow:

```text
inspect
-> mini plan
-> implement
-> verify
-> semantic review
```

## Change class C — architectural

Triggers include:

- new public abstraction,
- new dependency,
- new package,
- persistence model change,
- cross-domain dependency,
- new protocol,
- significant concurrency behavior,
- backwards compatibility change.

Workflow:

```text
inspect
-> architecture analysis
-> explicit design
-> implementation
-> architecture validation
-> full review
```

This avoids turning every two-line fix into bureaucracy while still preventing high-impact changes from being improvised.

---

# 10. Layer 5 — Require proof of reuse

This is one of the most important additions.

Before introducing code, a non-trivial plan should include:

```markdown
## Existing patterns inspected

- `src/runtime/controller.py`
- `src/runtime/run.py`
- `src/api/runs.py`

## Existing abstractions that could satisfy the requirement

### `RunController.cancel`

Why applicable:
- already owns externally initiated run lifecycle operations,
- used by both API and CLI,
- preserves the established dependency direction.

## New abstractions proposed

None.
```

If a new abstraction is necessary:

```markdown
## New abstraction proposed

`CancellationPolicy`

## Why existing abstractions are insufficient

`RunController` coordinates the operation but currently has no representation
for policy decisions shared by local and distributed runtimes.

The policy is used by three independent runtime implementations.

Alternatives considered:

1. Add conditional logic to each runtime.
   Rejected: duplicates policy and risks divergence.

2. Put policy inside `RunController`.
   Rejected: distributed runtimes also evaluate it without the controller.

3. Introduce `CancellationPolicy`.
   Selected because it centralizes one domain decision with three real callers.
```

This forces the agent to answer the question that generated code often skips:

> **Why does this new concept deserve to exist?**

A useful policy is:

```text
New abstraction with zero reuse evidence
    -> warning

New abstraction replacing an existing responsibility
    -> block

New abstraction with one caller and no explicit future requirement
    -> semantic review
```

The goal is not to prohibit abstraction.

The goal is to make abstraction **expensive in reasoning, not expensive in implementation**.

---

# 11. Layer 6 — Make architecture executable

Any rule that can be determined mechanically should leave the prompt and enter a tool.

## Example: Python layering with Import Linter

Import Linter supports contracts for layered architecture, including independence between sibling modules.[14]

Example `.importlinter`:

```ini
[importlinter]
root_package = agentdeck

[importlinter:contract:runtime-layers]
name = Runtime layers
type = layers
layers =
    agentdeck.api
    agentdeck.application
    agentdeck.domain

[importlinter:contract:domain-independent]
name = Domain modules are independent
type = independence
modules =
    agentdeck.domain.runs
    agentdeck.domain.sessions
    agentdeck.domain.events
```

Now this:

```python
# agentdeck/domain/runs.py
from agentdeck.api.http import Request
```

does not merely violate a style guideline.

It fails the architecture check.

---

## Example: machine-readable architecture contract

A tool-agnostic format could look like:

```yaml
version: 1

layers:
  - name: interface
    paths:
      - "src/api/**"
      - "src/cli/**"

  - name: application
    paths:
      - "src/application/**"

  - name: domain
    paths:
      - "src/domain/**"

  - name: infrastructure
    paths:
      - "src/adapters/**"

dependencies:
  allow:
    - interface -> application
    - application -> domain
    - infrastructure -> domain

  forbid:
    - domain -> infrastructure
    - domain -> interface
    - infrastructure -> interface

public_api:
  allowed_roots:
    - "src/public/**"

new_dependencies:
  require_plan: true
```

The same source could generate configuration for:

- Import Linter,
- dependency-cruiser,
- ArchUnit,
- custom AST rules,
- or repository-specific validators.

The important concept is:

> **Architecture should be data that CI can evaluate.**

---

# 12. Layer 7 — A code quality budget

"Keep the code clean" is too vague.

Track the structural cost of each change.

Example PR report:

```text
Repository Quality Delta
------------------------

Added LOC:                  +184
Deleted LOC:                -139
Net LOC:                     +45

New files:                     1
New public symbols:            0
New dependencies:              0
New architectural concepts:    0

Duplicate blocks:             +0
Dependency cycles:            +0
Architecture violations:       0

Functions complexity > 10:    +0
Unused exports:               +0

Comments added:                6
Narrative comments flagged:    1

Reuse candidates ignored:      0
```

This makes repository entropy visible.

## Good metrics

Possible measurements include:

| Metric | Why it matters |
|---|---|
| net LOC per capability | detects unnecessary expansion |
| new public symbols | approximates API surface growth |
| new abstractions | tracks conceptual growth |
| duplicate blocks | detects copy-local solutions |
| architecture violations | protects dependency direction |
| dependency cycles | detects structural coupling |
| unused exports/files | catches agent-created leftovers |
| comment-to-code delta | detects narration growth |
| complexity delta | catches local implementation inflation |
| churn within 30 days | identifies generated code repeatedly rewritten |
| reuse candidates ignored | measures failure to use canonical abstractions |

Do not use a single number as a universal "AI quality score."

Use a collection of signals and hard gates where appropriate.

---

# 13. Layer 8 — Comment policy based on information value

A comment count threshold is too crude.

Use a semantic rule.

## Allowed comments

Comments should preserve knowledge not obvious from the code:

```python
# Increment before dispatch so a process crash after send cannot
# cause the same attempt to be retried indefinitely.
retry_count += 1
```

Useful categories:

- rationale,
- invariant,
- external-system behavior,
- compatibility constraint,
- security reason,
- performance tradeoff,
- temporary workaround with removal condition.

## Disallowed comments

```python
# Increment the retry count
retry_count += 1
```

```python
# Return the result
return result
```

```python
# Loop through users
for user in users:
```

## Repository rule

```markdown
# Comment policy

Comments MUST communicate information that is not already evident from
the code and names.

Allowed reasons:
- WHY a non-obvious decision exists,
- an invariant that future changes must preserve,
- surprising external behavior,
- compatibility or security constraints,
- a workaround and the condition under which it can be removed.

Do not:
- narrate the next statement,
- restate function or variable names,
- explain standard language behavior,
- use comments as section headers where decomposition would be clearer.
```

A semantic reviewer can evaluate only newly added comments.

This keeps the cost low and avoids repeatedly reviewing the whole repository.

---

# 14. Hooks for guarantees at agent runtime

Some controls should happen before the agent edits.

Claude Code provides `PreToolUse` hooks that can deny an edit or command before it runs.[10]

A repository could use this to enforce process.

Example concept:

```bash
#!/usr/bin/env bash

INPUT="$(cat)"
FILE="$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')"

if [[ "$FILE" == src/* ]]; then
    if [[ ! -f ".agent-state/active-plan.json" ]]; then
        echo "Source edits require an active plan for non-trivial tasks." >&2
        exit 2
    fi
fi

exit 0
```

This exact implementation would need task classification so trivial edits are not blocked unnecessarily.

The larger point is:

```text
Instruction:
"Please plan first."

vs.

Lifecycle control:
"No source edit until planning gate is satisfied."
```

The second is substantially more reliable.

---

# 15. Deterministic checks versus semantic checks

Use this decision tree:

```text
Can the rule be determined from syntax, types, graph structure,
files, tests, or measurable thresholds?
               |
              YES
               |
               v
      deterministic validator

              NO
               |
               v
         semantic reviewer

Still ambiguous / high impact?
               |
               v
          human decision
```

Examples:

| Rule | Mechanism |
|---|---|
| domain must not import infrastructure | dependency graph |
| no dependency cycles | graph algorithm |
| all public APIs require tests | coverage/API tooling |
| no unused exports | static analysis |
| no new package without plan | git diff + plan metadata |
| comment restates code | semantic reviewer |
| new abstraction is unnecessary | semantic reviewer |
| API design conflicts with product direction | human/semantic review |
| architectural exception is justified | human approval |

This minimizes expensive model judgment and maximizes reproducibility.

---

# 16. Semantic review as the final quality layer

AI review is useful when used for the right problems.

CodeRabbit supports:

- repository coding guidelines,
- path-specific instructions,
- AST-based review instructions,
- custom pass/fail checks,
- learned review preferences.[15]

Qodo similarly describes a rule system built from codebase context, requirements, and pull-request history.[16]

Example CodeRabbit configuration:

```yaml
reviews:
  path_instructions:
    - path: "src/runtime/**"
      instructions: |
        Review only for repository-specific engineering issues:

        - Flag creation of helpers that duplicate an existing runtime utility.
        - Flag new lifecycle abstractions that overlap RunController.
        - Flag comments that merely narrate visible code.
        - Flag dependency access that bypasses the Store interface.
        - Prefer deletion or reuse over adding a second mechanism.

    - path: "src/public/**"
      instructions: |
        Any new public symbol must be intentional.
        Report:
        - the new public API,
        - its caller/use case,
        - whether an existing public API could satisfy the same requirement.
```

The reviewer should not spend tokens checking whether code formats correctly.

A formatter is better at that.

---

# 17. Continuous repository garbage collection

This is the layer that makes autonomy sustainable.

OpenAI's experience suggests that agent-generated repositories accumulate entropy even when individual changes are reviewed.[1]

Instead of periodic human cleanup days, run narrow maintenance agents.

Examples:

```text
cleanup/reuse
    Find local helpers added during the last 30 days that duplicate
    functionality in shared packages. Open one small PR per finding.

cleanup/comments
    Find newly introduced narrative comments that add no information.
    Remove only comments where behavior remains self-explanatory.

cleanup/architecture
    Identify dependencies that are technically permitted but inconsistent
    with the canonical pattern in docs/ARCHITECTURE.md.

cleanup/abstractions
    Find classes/interfaces with one implementation and one caller that
    add no meaningful boundary. Propose simplification; do not merge
    automatically.

cleanup/dead-code
    Remove unused exports, files, flags, compatibility branches, and
    abandoned experiments after verification.

cleanup/pattern-drift
    Compare implementations of the same repository concept and identify
    places where a second pattern has appeared.
```

Each cleanup job should:

1. scan,
2. produce evidence,
3. make the smallest possible change,
4. verify it,
5. open an isolated PR.

Do not ask one giant "clean the repository" agent to rewrite everything.

Small reversible PRs reduce risk.

---

# 18. Learning from review without creating invisible policy

CodeRabbit's "learnings" feature illustrates a useful pattern: review feedback can be remembered and reused in future reviews.[15]

However, important engineering rules should not live only inside a vendor's private memory.

Use a promotion loop:

```text
review correction
      |
      v
temporary learning
      |
      v
did it recur?
  |         |
 NO        YES
  |         |
keep       v
local   classify rule
            |
            +--> deterministic? -> validator/CI
            |
            +--> architecture?  -> INVARIANTS.md
            |
            +--> pattern?       -> docs/patterns/
            |
            +--> judgment?      -> semantic review rule
```

This creates a repository that **learns from mistakes**.

A repeated review comment is a signal that the repository harness is missing something.

---

# 19. A complete example repository

```text
my-sdk/
│
├── AGENTS.md
├── INVARIANTS.md
├── README.md
├── Makefile
├── pyproject.toml
│
├── .engineering/
│   ├── architecture.yaml
│   ├── quality.yaml
│   ├── comment-policy.md
│   └── change-classes.yaml
│
├── .agents/
│   └── skills/
│       ├── feature/
│       │   └── SKILL.md
│       ├── refactor/
│       │   └── SKILL.md
│       └── cleanup/
│           └── SKILL.md
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ENGINEERING.md
│   ├── TESTING.md
│   │
│   ├── patterns/
│   │   ├── lifecycle.md
│   │   ├── persistence.md
│   │   ├── errors.md
│   │   └── public-api.md
│   │
│   ├── decisions/
│   │   ├── 001-run-controller.md
│   │   └── 002-store-interface.md
│   │
│   └── plans/
│       ├── active/
│       │   └── add-run-timeout.md
│       └── completed/
│
├── scripts/
│   ├── check_architecture.py
│   ├── quality_delta.py
│   ├── check_comments.py
│   └── repo_map.py
│
├── src/
│   └── ...
│
└── tests/
```

---

# 20. Example `INVARIANTS.md`

```markdown
# Repository invariants

These rules are architectural constraints, not style preferences.

## Dependency direction

- Domain code does not import adapters, HTTP, CLI, or database implementations.
- Application code may depend on domain interfaces.
- Infrastructure implements domain/application interfaces.
- Public surfaces call application services; they do not reach stores directly.

## Canonical ownership

- Run lifecycle operations are coordinated by `RunController`.
- Persistence is accessed through `RunStore`.
- Event history is append-only.
- External data is validated at the boundary.

## Concept creation

A new public abstraction requires a change plan containing:
- existing abstractions considered,
- why each is insufficient,
- concrete callers,
- the invariant the new abstraction owns.

## Dependencies

New runtime dependencies require explicit justification in the change plan.

## Comments

Comments explain rationale, invariants, or surprising external behavior.
Narration of visible code is prohibited.
```

---

# 21. Example lightweight change plan

OpenAI's ExecPlan approach is designed for complex, multi-hour changes and records progress, discoveries, design decisions, and outcomes.[19]

A normal repository does not need that weight for every change.

A lighter form can be:

```markdown
# Add timeout support to Run invocation

## Goal

Allow callers to set a timeout for a run without introducing a second
cancellation mechanism.

## Current architecture inspected

- `src/runtime/controller.py`
- `src/runtime/run.py`
- `src/runtime/timers.py`
- `docs/patterns/lifecycle.md`

## Existing patterns

`RunController.cancel()` is the canonical cancellation path.

`TimerService` already schedules runtime callbacks.

## Reuse decision

Use `TimerService` to trigger `RunController.cancel()`.

Do not add:
- `TimeoutManager`,
- a second run state transition path,
- direct timer logic inside `Run`.

## Files expected to change

- `src/runtime/controller.py`
- `src/runtime/timers.py`
- `tests/runtime/test_timeout.py`

## New abstractions

None.

## Expected structural delta

- no new runtime dependency,
- no new public class,
- no new architectural edge,
- <100 net LOC expected.

## Verification

- timeout cancels a running run,
- normal completion clears the timer,
- timeout does not double-cancel,
- existing cancellation tests remain unchanged.
```

Notice what this plan does.

It describes not only **what will be added**, but also **what must not be added**.

That is valuable with generative systems.

---

# 22. Example of a bad agent change versus an aligned change

Assume the task is:

> Add timeout support to runs.

## Unaligned agent

The agent searches for "timeout", finds no implementation, and creates:

```text
src/runtime/timeout_manager.py
src/runtime/timeout_config.py
src/runtime/timeout_exceptions.py
```

It adds:

```python
class TimeoutManager:
    ...
```

and directly mutates run state when the timer expires.

The feature works.

Tests pass.

But the repository now has two lifecycle paths:

```text
RunController.cancel()
```

and:

```text
TimeoutManager._terminate_run()
```

Six months later the two paths behave differently.

## Aligned agent

The harness supplies:

```text
Canonical lifecycle owner:
RunController

Existing timer abstraction:
TimerService

Architecture invariant:
all externally initiated lifecycle transitions flow through RunController
```

The change analysis says:

```text
Reuse candidate: RunController.cancel
Reuse candidate: TimerService.schedule
New abstraction required: no
```

Implementation becomes approximately:

```python
timer = timers.schedule(
    timeout,
    lambda: controller.cancel(run_id, reason="timeout"),
)
```

with appropriate lifecycle and race handling.

The feature is not merely correct.

It is expressed in the vocabulary the repository already uses.

That is the desired outcome.

---

# 23. Single execution surface

Agents should not guess how to validate the repository.

Provide one canonical entry point:

```makefile
.PHONY: check

check:
	ruff check .
	mypy src
	python scripts/check_architecture.py
	pytest
	python scripts/quality_delta.py --check
```

Then `AGENTS.md` needs only:

```markdown
Before completion, run:

    make check

Do not reproduce individual validation commands elsewhere.
```

This gives humans and all agents the same definition of "repository-valid."

Adobe's repository harness guide similarly recommends a single execution surface such as a `Makefile` as part of a production harness.[7]

---

# 24. Quality gates for new code, not historical perfection

A mature codebase may already contain architectural debt.

If every new quality rule immediately requires fixing all old violations, adoption becomes impractical.

Instead establish:

```text
existing debt
    = tracked baseline

new violation
    = failure
```

For example:

```yaml
quality:
  architecture:
    new_violations: 0

  duplication:
    max_new_duplicate_blocks: 0

  dependencies:
    new_cycles: 0

  comments:
    narrative_comment_findings:
      severity: review

  complexity:
    max_new_function_complexity: 12
```

This follows the general "clean as you code" idea used by modern quality-gate systems: prevent new debt while reducing existing debt incrementally.

---

# 25. Multi-agent repositories need coordination metadata

As parallel agents become common, a repository harness should expose current work.

Example:

```text
.agent-work/
  active/
    run-timeouts.yaml
    postgres-recovery.yaml
```

```yaml
id: run-timeouts
owner: agent-42
status: implementing

touches:
  - src/runtime/controller.py
  - src/runtime/timers.py

concepts:
  - run-lifecycle
  - cancellation

plan:
  docs/plans/active/add-run-timeouts.md
```

Before another agent begins work, it can detect:

```text
Conflict:
both plans modify run-lifecycle semantics.
```

This will not eliminate semantic conflicts, but it provides a coordination layer above Git's line-level merge model.

Given the 2026 evidence that cross-agent co-active PR pairs had substantially higher textual conflict rates than intra-agent pairs, this is likely to become increasingly important.[6]

---

# 26. Proposed end-to-end agent workflow

```text
USER TASK
   |
   v
CLASSIFY CHANGE
   |
   +--> trivial ------------------------------+
   |                                         |
   +--> normal --> repository inspection      |
   |              |                          |
   |              v                          |
   |          reuse analysis                  |
   |              |                          |
   |              v                          |
   |           mini plan                      |
   |                                         |
   +--> architectural -> full design ---------+
                         |
                         v
                  IMPLEMENTATION
                         |
                         v
                 deterministic checks
                         |
           +-------------+-------------+
           |                           |
         fail                         pass
           |                           |
           v                           v
     agent fixes                semantic review
                                       |
                            +----------+----------+
                            |                     |
                          fail                  pass
                            |                     |
                            v                     v
                         fix/replan          PR/merge
                                                  |
                                                  v
                                         entropy scanners
                                                  |
                                                  v
                                        small cleanup PRs
```

The key property is that **generation is only one stage**.

The repository system surrounds generation with discovery, constraints, and feedback.

---

# 27. A practical adoption plan

Do not build the entire system at once.

## Phase 0 — Baseline

Measure current agent behavior for 20–50 PRs:

- net LOC,
- comments added,
- new abstractions,
- duplicate code,
- architecture violations,
- review corrections,
- rework within 30 days.

This gives you a comparison point.

---

## Phase 1 — Knowledge

Add:

```text
AGENTS.md
INVARIANTS.md
docs/ARCHITECTURE.md
docs/patterns/
```

Keep `AGENTS.md` small.

Document only decisions the agent cannot reliably infer.

---

## Phase 2 — One validation command

Create:

```text
make check
```

containing:

- formatter/linter,
- type checker,
- unit/integration tests,
- architecture checks.

Every agent uses the same command.

---

## Phase 3 — Architecture contracts

Encode:

- dependency direction,
- forbidden module access,
- cycles,
- public API ownership,
- repository boundaries.

Start by preventing **new** violations.

---

## Phase 4 — Plan gate

For non-trivial work, require:

```text
Existing patterns inspected
Existing abstractions considered
Reuse decision
New abstractions proposed
Expected files
Verification
```

Do not require a large spec for small changes.

---

## Phase 5 — Semantic quality review

Add focused checks for:

- narrative comments,
- unjustified abstractions,
- duplicate concepts,
- inappropriate new dependencies,
- violations of repository-specific patterns.

Avoid generic "best practices" review prompts.

---

## Phase 6 — Repository map

Build or integrate:

- symbol extraction,
- dependency graph,
- relevant code ranking,
- architecture metadata,
- recent change context.

The objective is to improve discovery before implementation.

Aider's repo-map implementation is a concrete proof that compact, graph-ranked symbol context can help models use existing repository abstractions.[12]

---

## Phase 7 — Continuous garbage collection

Schedule narrow maintenance scans.

Examples:

```text
daily:
  new architecture drift

weekly:
  duplicate helpers
  dead code
  narrative comments
  unnecessary abstractions

monthly:
  architecture quality review
  stale documentation
  pattern consolidation
```

The output should be small PRs, not reports nobody reads.

---

# 28. What should be measured

If the harness works, the improvement should be visible.

Recommended metrics:

## Structural

```text
architecture violations / PR
new dependency cycles / PR
new public abstractions / 1k changed LOC
duplicate code introduced / PR
unused symbols introduced / PR
```

## Agent behavior

```text
% non-trivial plans containing reuse candidates
% new abstractions with explicit rationale
% changes using canonical repository pattern
% PRs requiring architecture correction
```

## Maintainability

```text
30-day churn of agent-authored code
revert rate
follow-up bug-fix rate
cleanup LOC / feature LOC
review comments about duplication
review comments about over-engineering
```

## Comment quality

```text
comments added / 1k LOC
narrative comments flagged
comment deletions within 30 days
```

## Human cost

```text
review time / PR
manual cleanup hours / week
architecture-review escalations
```

The critical metric is not:

```text
How much code did the agent produce?
```

It is:

```text
How much repository entropy did each unit of useful capability create?
```

---

# 29. Existing solutions and what each contributes

| Solution | Useful idea | Missing piece |
|---|---|---|
| OpenAI harness engineering | repository knowledge, architecture enforcement, golden principles, cleanup agents | internal implementation is not a reusable standard |
| Adobe Repository Harness Guide | full repository-local harness framing and reference layout | general framework; repository-specific architectural tooling still required |
| GitHub Spec Kit | constitution + structured spec/plan/tasks/convergence | can be heavyweight for routine edits; not a complete architecture enforcement system |
| Kiro Specs | persistent requirements/design/tasks and execution verification | primarily tied to Kiro workflow |
| Claude Code | concise repo instructions, plan mode, deterministic hooks | repository engineering policy still has to be designed by the project |
| Aider Repo Map | compact symbol/dependency context to improve existing-abstraction discovery | does not encode your engineering constitution |
| Aider Architect Mode | separates reasoning from editing; benchmark improvements reported by Aider | not a repository governance system |
| Import Linter / ArchUnit / dependency tools | deterministic architecture rules | cannot judge semantic taste |
| CodeRabbit | path rules, AST rules, custom checks, learnings | AI review remains probabilistic; vendor memory should not be the sole source of truth |
| Qodo | repository-aware rule system and multi-agent review | review happens after or around generation rather than defining the whole development harness |

The opportunity is the integration of these ideas into one repository-owned system.

---

# 30. What not to do

## Do not create a 2,000-line `AGENTS.md`

Important rules will compete with unimportant rules.

Use progressive disclosure.

---

## Do not ask AI to enforce machine-checkable rules

Bad:

```text
Please ensure there are no circular dependencies.
```

Better:

```text
python scripts/check_cycles.py
```

---

## Do not require a full spec for every edit

Process overhead becomes a reason to bypass the process.

Classify changes by risk.

---

## Do not let review memory become the architecture source of truth

If a rule matters, promote it into the repository.

---

## Do not treat current code as automatically canonical

Agents copy patterns.

If the repository contains three legacy patterns, document which one is current.

---

## Do not optimize for minimum LOC blindly

Sometimes correct architecture requires more code.

The target is **justified complexity**, not code golf.

---

## Do not automatically merge semantic cleanup with broad scope

"Remove all unnecessary abstractions" is dangerous.

Use evidence, narrow scope, and small PRs.

---

# 31. The proposed minimal standard

If only a small version of this system can be implemented, start with these six requirements.

## Requirement 1 — Repository map

Agents must know where architecture, patterns, invariants, and tests live.

## Requirement 2 — Reuse evidence

Every non-trivial change answers:

```text
What existing code/patterns did you inspect?
What can be reused?
Why is anything new necessary?
```

## Requirement 3 — Architecture as CI

At least the most important dependency boundaries are machine-enforced.

## Requirement 4 — Plan before meaningful structural changes

New public abstractions, dependencies, packages, and cross-module changes require a design artifact.

## Requirement 5 — One verification command

Humans and agents share the same repository acceptance gate.

## Requirement 6 — Recurring entropy cleanup

The repository is periodically scanned for drift and corrected in small changes.

Those six controls would already solve a significant portion of the problem.

---

# 32. Final conclusion

The next major improvement in AI coding will not come only from models that can generate more code.

Generating code is already becoming cheap.

The scarce resource is **coherence**.

A codebase is the accumulated result of thousands of decisions:

- where responsibilities belong,
- which concepts exist,
- which concepts deliberately do not exist,
- what is shared,
- what must remain independent,
- what deserves a comment,
- what is allowed to depend on what,
- which old paths are being retired,
- and why strange-looking constraints exist.

A human team traditionally carries much of this information socially and historically.

Autonomous agents do not reliably inherit that context.

Therefore the repository must carry it.

The strongest solution suggested by current industry practice and research is:

> **Move engineering judgment from transient agent prompts into a versioned, inspectable, executable repository control system.**

The agent should not be asked merely to "write clean code."

It should enter an environment where:

```text
the architecture is explicit,
the relevant existing abstractions are discoverable,
new concepts require evidence,
hard rules are mechanically enforced,
semantic smells are reviewed,
and repository entropy is continuously collected.
```

At that point, different coding agents can change over time without changing the engineering contract.

Codex can write the next PR.

Claude can write the one after it.

A human can write the third.

All three are extending the **same software system**, rather than independently generating code that happens to live in the same Git repository.

---

# Appendix A — Suggested starter files

```text
AGENTS.md
INVARIANTS.md
docs/ARCHITECTURE.md
docs/ENGINEERING.md
docs/patterns/
docs/plans/active/
.engineering/architecture.yaml
.engineering/quality.yaml
Makefile
```

---

# Appendix B — Suggested engineering principles

```markdown
# Engineering principles

1. Reuse before creation.
2. One concept should have one canonical representation.
3. New abstractions require concrete responsibility and callers.
4. Architecture boundaries are stronger than local convenience.
5. Prefer deletion and consolidation over parallel mechanisms.
6. Comments preserve reasoning; code preserves behavior.
7. Avoid speculative generality.
8. Public API surface is a cost.
9. Dependencies are architectural decisions.
10. Every meaningful change must be verifiable.
11. Repeated review feedback must become repository knowledge or tooling.
12. Technical debt should be removed continuously, not periodically.
```

---

# Appendix C — Suggested change-analysis contract

```yaml
change:
  goal: ""

  inspected:
    files: []
    docs: []
    symbols: []

  existing_patterns: []

  reuse_candidates:
    - symbol: ""
      decision: reuse | reject
      reason: ""

  new_abstractions:
    - name: ""
      reason_existing_is_insufficient: ""
      callers: []
      invariant_owned: ""

  architecture:
    new_edges: []
    new_dependencies: []

  expected_delta:
    files_added: 0
    public_symbols_added: 0
    approximate_net_loc: 0

  verification:
    commands: []
    behaviors: []
```

This could be produced automatically by the planning agent and validated before editing begins.

---

# References

1. **OpenAI — "Harness engineering: leveraging Codex in an agent-first world"** (Feb. 11, 2026). Production case study describing a roughly million-line agent-generated repository, short `AGENTS.md` as a map, mechanically enforced architecture, "golden principles," and recurring cleanup tasks.
   https://openai.com/index/harness-engineering/

2. **"Echoes of AI: Investigating the downstream effects of AI assistants on software maintainability" — Empirical Software Engineering** (2026). Reports, among other observations, comments added by 79% of AI-assisted developers versus 54% of non-AI developers in one detailed task analysis.
   https://link.springer.com/article/10.1007/s10664-026-10889-1

3. **"An exploratory study on LLM-generated code and comments in code repositories" — Journal of Systems and Software** (2026). Detector-based proxy analysis reporting substantial intra-repository clones in code detected as likely LLM-generated.
   https://www.sciencedirect.com/science/article/pii/S0164121226002591

4. **Zhu, Tsantalis, Rigby — "AI-Generated Smells: An Analysis of Code and Architecture in LLM and Agent-Driven Development"** (arXiv preprint, 2026). Studies technical debt and structural degradation in generated code. Treat broad causal claims as preprint evidence rather than settled consensus.
   https://arxiv.org/abs/2605.02741

5. **Ogenrwot & Businge — "How AI Coding Agents Modify Code: A Large-Scale Study of GitHub Pull Requests"** (2026). Analysis of 24,014 merged agentic PRs and 5,081 merged human PRs.
   https://arxiv.org/abs/2601.17581

6. **Xu, Subramanian, Karthik — "AI Agent Pull Requests on GitHub: Frequency, Structure, and Merge Conflict Rates"** (2026). Reports higher textual conflict rates for replayed cross-agent co-active PR pairs than intra-agent pairs.
   https://arxiv.org/abs/2607.04697

7. **Adobe — "Repository Harnesses for AI Coding Agents: A Practical Guide"** (v1.1.0, Aug. 2026). Repository-local control-system framework covering agent guidance, invariants, skills, docs, execution surfaces, sensors, and maintenance.
   https://opensource.adobe.com/ai-repo-harness-guide/

8. **GitHub — Spec Kit: Agentic Spec-Driven Development**. Defines constitution → specification → planning → tasks → analysis → implementation → convergence workflows and quality gates.
   https://github.github.com/spec-kit/reference/agentic-sdd.html
   https://github.com/github/spec-kit

9. **Anthropic — Claude Code Best Practices**. Recommends explore → plan → implement for complex changes and concise always-loaded repository guidance.
   https://code.claude.com/docs/en/best-practices

10. **Anthropic — Claude Code Hooks**. Documents `PreToolUse` and other hooks that can deterministically block or modify agent actions.
    https://code.claude.com/docs/en/hooks
    https://code.claude.com/docs/en/hooks-guide

11. **Kiro — Specs documentation**. Persistent requirements, design, tasks, and sequential execution with verification.
    https://kiro.dev/docs/cli/v3/specs/
    https://kiro.dev/docs/web/specs/

12. **Aider — Repository Map**. Uses Tree-sitter-derived symbols and graph ranking to provide concise codebase context and help models use existing modules and abstractions.
    https://aider.chat/docs/repomap.html
    https://aider.chat/2023/10/22/repomap.html

13. **Aider — Architect Mode / separating code reasoning and editing**. Reports benchmark improvements for several Architect/Editor model pairings over solo baselines.
    https://aider.chat/2024/09/26/architect.html

14. **Import Linter — Contract Types**. Provides executable architectural contracts including layered architecture and independent modules for Python.
    https://import-linter.readthedocs.io/en/v2.3/contract_types.html

15. **CodeRabbit documentation**. Repository/path instructions, AST-based instructions, custom checks, code guidelines, and persistent review learnings.
    https://docs.coderabbit.ai/configuration/path-instructions
    https://docs.coderabbit.ai/configuration/ast-grep-instructions
    https://docs.coderabbit.ai/pr-reviews/custom-checks
    https://docs.coderabbit.ai/knowledge-base/learnings

16. **Qodo Code Review documentation**. Describes a rule system drawing on repository context, requirements, and pull-request history for consistent review.
    https://docs.qodo.ai/code-review

17. **Anthropic — How Claude remembers your project / configuration guidance**. Distinguishes instruction context from permissions/hooks that provide enforceable boundaries.
    https://code.claude.com/docs/en/memory
    https://code.claude.com/docs/en/debug-your-config

18. **Adobe — Harness Components / Documentation Prompts**. Recommends canonical ownership for repository knowledge and progressive disclosure.
    https://opensource.adobe.com/ai-repo-harness-guide/04-Harness-Components/
    https://opensource.adobe.com/ai-repo-harness-guide/skills/harness-setup/references/docs/

19. **OpenAI — "Using PLANS.md for multi-hour problem solving"**. Defines ExecPlans as living design documents containing progress, discoveries, decision logs, outcomes, explicit context, work plans, and verification.
    https://developers.openai.com/cookbook/articles/codex_exec_plans

---

## Evidence caveats

The sources above are not all equivalent.

- OpenAI's harness article is a production case study, not a controlled experiment.
- Adobe, Anthropic, GitHub, Kiro, Aider, CodeRabbit, and Qodo documentation describe engineering techniques and product behavior, not independent proof that each technique improves maintainability in every repository.
- Several cited 2026 studies are recent preprints and should not be treated as final scientific consensus.
- The LLM-code clone study uses detector-based proxy analysis; LLM-origin detectors are imperfect.
- Maintainability is multi-dimensional and cannot be reduced to LOC, comments, or complexity alone.

The value of the evidence is the **convergence of independent observations**:

1. context and repository discovery matter,
2. planning before implementation helps on non-trivial changes,
3. deterministic constraints outperform advisory prompts for hard rules,
4. architectural knowledge must be explicit,
5. agent-generated repositories accumulate entropy,
6. continuous feedback and cleanup are necessary for long-running autonomous development.

That convergence is the basis for the Repository Engineering Harness proposed here.
