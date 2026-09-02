# Overrun: from size to cause

Size is a trigger, never a finding. A BLOCK on LOC gives the author one move, splitting the diff,
and a PR whose size came from a missing concept splits into two PRs that are still the wrong shape.
The number sends you looking. What you find is the finding.

## The trigger

Measured delta exceeds the PR's `## Expected delta` or `## Concept budget` by roughly 2x and the
body does not explain it. Both were written into the draft body before the first source edit and
cleared the design gate, so the overrun is against a real prediction. Nothing else fires this pass:
a large diff landing inside its own declaration is a PR that estimated correctly, and a body with
no estimate never ran Stage 1, so there is nothing to overrun.

## The pass

Pause line-level review and resume Attack and Craft after: an oversized PR must not end up with
less correctness review than a small one. Read the issue's `## Proposed shape`, the PR's `## Design`
and `## Analog`, the `docs/design/` entry if there is one, and the decisions recorded on both. The
question is not "which lines could go" but "what did this design ask for that cost this much".

| where the overrun sits | inspect for | remedy |
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

The cause carries no class of its own; it classifies under the finding-class table like anything
else. A cause touching public API shape is a DISCUSS carrying the smaller design, one that does not
is a BLOCK whose consequence is the code the wrong shape produced, and either way the fix is the
design rather than the split. A cause that is real but out of this PR's reach is a DEFER carrying
the design question, never a NIT: a NIT is small and optional, and a design costing 2x its estimate
is neither.
