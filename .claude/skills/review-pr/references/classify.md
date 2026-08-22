# Classify: four worked examples from the v5 milestone

## BLOCK: #420, `Deck._mint` against an empty catalog

`agentdeck/deck.py:984` passed `catalog={}` when compiling a minted agent, so forking any agent
that declares `subagents=` raised `NotFoundError: No agent named 'Researcher'. Available: [].`
against a deck whose catalog held `Researcher`. `Writer` with `subagents=["Researcher"]` was
#236's own headline example, so this was the feature's main case, not a corner. A named
consequence, introduced by this PR, verified by reproducing it: BLOCK, not a DISCUSS. The fix
(`self._agents` is already in scope at that line) is the author's, not the reviewer's.

## Should have been DISCUSS: #415, "block, narrowly"

A re-review at a later SHA read: "block, narrowly. The ERROR is genuinely fixed... What holds the
marker is one small thing carried over from the last round that the fix widened rather than
closed, plus a predicate that is broader than the rule its own docstring states." That is a
reviewer who is not actually asserting a defect with a named consequence; it is hedging on a call
it was not sure blocked. Under the old vocabulary there was no honest slot for that, so it came
out as a soft BLOCK. Under BLOCK/DISCUSS/DEFER/NIT it is a DISCUSS: state what would settle it
(does the widened predicate admit a case the docstring's rule excludes, yes or no), and let the
author answer.

## Correctly not filed: #420, the `close_cancelled` bypass ruling

`Runtime._record`'s abandonment guard runs before the roll-up fold, so a run closed via
`close_cancelled` never contributes to a parent's total. The reviewer traced the consequence
(one un-popped `_tree` entry on a map discarded with the `Deck` in the same breath, not a leak,
nothing mis-billed) and ruled: "Acceptable as it stands." No DEFER, because there was no real
defect to hand off, only a bypass whose cost the reviewer had already priced at zero. Filing an
issue here would have been process for its own sake.

## NIT: #420, a body-precision miss

The PR body's `## Recorded judgments` claimed two rulings were "amended into the design doc."
Only one row landed there; the pause ruling did not. Real, and worth one line inline, but it cost
nothing to fix, blocked nothing, and needed no author reply beyond editing the doc. That is a NIT:
collapse it into the bottom list, not a numbered WARNING in the rubric.
