---
name: ship-pr
description: Implement one agentdeck GitHub issue end to end as a PR: design in the PR body before code, code that matches its analog, self-review that catches the path no test exercises, then the merge gate. Five stages (understand, design, implement, self-review, gate). Use when implementing an agentdeck issue as deck-dev.
---

# Ship a PR

Design the diff before writing it. The design binds nothing by itself, so Stage 1 stops and
waits for the orchestrator to check it against the brief before Stage 2 touches source.
Self-review is a checklist, not reflection: an unanswered question fails the stage, same as an
unrun command.

## Checklist

Copy this into your own response and check off each stage as you complete it:

```
Ship progress:
- [ ] 0. Understand: spec gate, engineering docs, seed the worktree
- [ ] 1. Design: PR body sections, then stop and wait for the design gate
- [ ] 2. Implement: match the analog, stay in budget (references/implement.md)
- [ ] 3. Self-review: ten shape questions plus the coverage question (references/self-review.md)
- [ ] 4. Gate: make check green, gh pr ready
```

## Freedom per stage

Thinking is high freedom. Verification and output are low: a checklist or a template you
improvise is not one.

| stage | freedom | why |
|---|---|---|
| 0 Understand | LOW on the spec gate, HIGH on reading | the gate is binary: `Done when` and scope bounds present, or stop and comment |
| 1 Design | LOW on format, HIGH on content | CI parses the budget block; what argument goes in it is judgment |
| 2 Implement | HIGH | open field. Over-specifying here does real damage, and the issue this skill ships from puts it out of scope |
| 3 Self-review | LOW | a checklist you can improvise is not a checklist |
| 4 Gate | LOW | exact commands, fragile sequence |

## Register

Every instruction below, and in `references/`, carries exactly one of these:

| register | wording | carries |
|---|---|---|
| CONSTRAINT | MUST / NEVER | what breaks if violated, named alongside it |
| EXPECTATION | "the reviewer checks X" | which finding class the miss earns |
| JUDGMENT | "default is X, deviate when Y" | nothing; your call |

A CONSTRAINT without a stated consequence is a preference, not a CONSTRAINT: rewrite it or
demote it.

## Stages

| stage | freedom / register | does | writes |
|---|---|---|---|
| 0. Understand | LOW / CONSTRAINT on the gate | Read the issue as spec. Spec gate below. Seed the worktree. | nothing, or a gate comment |
| 1. Design | LOW / CONSTRAINT on format | PR body sections, then stop. `references/design.md` | draft PR, then wait |
| 2. Implement | HIGH / JUDGMENT | Match the analog, stay in budget, test what the change exposes. `references/implement.md` | source, tests, CHANGELOG |
| 3. Self-review | LOW / CONSTRAINT | Ten shape questions plus the coverage question, answered in writing. `references/self-review.md` | the answers, visible in your response |
| 4. Gate | LOW / CONSTRAINT | `make check` green, compact the body, `gh pr ready`. Below. | ready PR |

## Stage 0: Understand

CONSTRAINT: read the issue (`gh issue view <n>`), `docs/engineering/` in full, and the
`docs/patterns/` file for your concern, before any edit. Consequence: skip it and Stage 1's
design has nothing real to bind against, and the design gate has nothing to compare it to.

CONSTRAINT, spec gate: if the issue lacks `Done when` outcomes or scope bounds (what must NOT be
added), comment on the issue naming exactly what is missing and stop. Consequence of skipping
this: an unbounded spec produces unbounded code, and the reviewer's Scope phase will file it.

Seed the worktree: copy `.env` if present, then `uv venv --python 3.12 && make install`.

## Stage 1: Design, then wait

Run `uv run scripts/repomap.py`. Open a draft PR (branch `feat/<n>-<slug>` or `fix/<n>-<slug>`,
`gh pr create --draft` targeting `dev`, `Closes #<n>`) and write the complete design into its body
before touching source. Section shape, caps, and the design gate: `references/design.md`.

CONSTRAINT: after posting the design, stop. Do not edit source until the orchestrator (or the
user, outside `ship-issue`) tells you to proceed. Consequence of skipping this: bloat argued
against code that already works never gets fixed; catching it at design costs nothing.

## Stage 2: Implement

JUDGMENT, high freedom. Depth and test rules: `references/implement.md`.

## Stage 3: Self-review

CONSTRAINT: answer every question in `references/self-review.md` in writing before Stage 4,
including the coverage question. An unanswered question fails the stage.

## Stage 4: Gate

- Compact the PR body: it describes the change as it now stands, not the rounds that produced it.
  Delete abandoned design and justifications the code no longer needs.
- CONSTRAINT: `make check` 100% green before `gh pr ready`. Consequence: `gh pr ready` on a red
  `make check` fails the Agent review gate and the PR cannot merge.
- JUDGMENT: if `dev` has advanced past your branch, run `gh pr update-branch --rebase`, not the
  plain merge default. A merge commit moves the head SHA and invalidates a review's PASS marker
  for no code reason.
- No attribution trailers anywhere.

## Answering a review

Reply in the review thread and resolve it. Never grow the PR body to answer a finding: a thread
collapses once addressed, a body is what the next reader wades through to learn what the change
does. The body only changes when the change does.

CONSTRAINT: resolve every thread, or the PR cannot merge. `dev` enforces this twice, as
`required_conversation_resolution` in branch protection and `required_review_thread_resolution` in
its ruleset, so one open thread holds the PR at `BLOCKED` with every check green, zero required
approvals, and nothing in `gh pr checks` to explain it. Fixing the code is not answering the
finding; the thread is.

```bash
gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}' -f t="<thread-id>"
```

Thread ids come from `reviewThreads` on the pull request. Reply first, then resolve: a resolved
thread with no reply tells the next reader nothing about what changed.

## Measured by

Neither agent scores itself. What the dev side of this skill is measured on, from #431:

| metric | source |
|---|---|
| BLOCK count on first review | reviewer's verdict marker |
| rework ratio: commits after `ready_for_review` / total | git log vs. the ready event |
| declared vs. actual concept budget | `concept_budget.py` |
| declared vs. actual LOC | `quality_delta.py` |
| files touched vs. files the issue named | diff vs. issue body |
| PR body words vs. cap | body, fences stripped |
| `make check` green before `ready`? | check runs vs. ready timestamp |
| tokens, tool calls, wall time | agent completion record |

**Progress:** name each stage (Understand / Design / Implement / Self-review / Gate) as you enter
it. A silent multi-stage run reads as a stall.
