# Overrun: from size to cause

Size is a trigger, never a finding. A BLOCK on LOC gives the author one move, splitting the diff,
and a PR whose size came from a missing concept splits into two PRs that are still the wrong shape.
The number sends you looking. What you find is the finding.

## The trigger

Measured delta exceeds the issue's `## Expected delta`, or the PR's `## Concept budget`, by roughly
2x, and the body does not explain it. Nothing else fires this pass: a large diff that lands inside
its own declaration is a PR that estimated correctly.

## The pass

Stop reading the diff. Read the issue's `## Proposed shape`, the `docs/design/` entry if there is
one, and the decisions recorded on the issue and the PR. The question is not "which lines could
go" but "what did this design ask for that cost this much".

| where the overrun sits | cause | remedy |
|---|---|---|
| one new module | a concept the design never named | name it in the design first |
| spread evenly across files | a rename or refactor riding along | split |
| tests grew faster than behavior | the contract is unclear, or the abstraction is wrong | fix the abstraction |
| many new public symbols | `## Reuse analysis` missed an existing sibling | cite the sibling |
| growth arrived in later commits | patched through review rounds, never designed | consolidation pass |

Spawning a critic for this pass is fine and it passes `model: "sonnet"` like any other subagent.

## It ends in a smaller design or it ends in nothing

The pass returns the shape the code should have had: which concept was missing, which existing
abstraction covers it, what the public surface becomes. A pass that returns "too big" has not run.
If the design holds up and the size is simply what the feature costs, say so and record the number
against the estimate; the estimate was wrong, not the code.

## Classifying what it finds

The cause blocks like any other BLOCK, and its consequence is the code the wrong shape produced.
The fix is the smaller design, not the split. An overrun whose cause is real but out of this PR's
reach is a DEFER carrying the design question, never a NIT: a NIT is small and optional, and a
design that costs 2x its estimate is neither.
