# Comment/docstring density ratio gate

## Context

SLOP010/SLOP011 (#520) cap the size of one docstring or one comment block, but nothing caps a
file's aggregate share of comment+docstring lines versus code. A file can pass every per-block
cap while still being mostly prose if the bloat is spread across many small, individually
compliant blocks. Two checks, not one: the ratio of the whole file once touched, and the ratio
within a PR's own diff, since either alone misses a failure mode the other catches.

## Decision: two independent checks, both gated on the file being touched

**Check A: whole-file ratio.** When a PR touches a file at all, measure that file's overall
comment+docstring line share of its total lines, in its state after the edit. Same gating
discipline as SLOP010/011: a file nobody edits is never retroactively broken, only a file this
PR actually changes is measured. Catches death-by-a-thousand-small-blocks: many
individually-compliant comments that still bloat the file in aggregate.

**Check B: diff-only ratio.** Of the lines this PR itself adds, what fraction are
comment/docstring versus code. `scripts/quality_delta.py` already counts "comment lines added"
as an informational number in the PR body; this turns that into an actual threshold. Needed
because Check A alone can dilute a comment-heavy diff into an acceptable file-level number if the
surrounding file is large.

## Metric definition

Numerator: every full-line `#` comment plus every docstring line, `Args:`/`Returns:`/etc.
sections included. Unlike SLOP010, nothing here is exempted: a ratio check is about total prose
volume, not "is this a design essay", so a long Google-style parameter list counts toward it even
though SLOP010 lets it through.

Denominator: total lines in scope (file for Check A, added lines for Check B). Pick blank-line
handling empirically rather than guessing; state whichever choice is made and why.

Library-only (`agentdeck/`), same predicate `check_file`'s `_scope` already uses for
SLOP004/010/011.

## Threshold

No existing repo convention states a ratio number the way CLAUDE.md's "max 1-2 lines" gave SLOP011
its cap. Derive one empirically before picking it: measure the actual current ratio distribution
across `agentdeck/` (median, p90 per file), and choose a threshold that does not fail the bulk of
today's code outright. Same verification discipline #520 used to confirm its own rules were
non-disruptive (173/151 latent instances, none currently blocking). Do not invent a round number
ungrounded in the codebase's real shape.

## Rule number

SLOP012. (SLOP007 is unused/retired in `scripts/slopcheck.py`; 001-006 and 008-011 are taken.)

## Non-goals

Not retroactive: existing files stay green until a PR touches their own lines, same discipline as
every other library-only SLOP rule. Not a whole-repo dashboard or metrics report: this is a gate,
not a reporting tool.

## Verification

| Check | How |
|---|---|
| Threshold is grounded, not invented | empirical ratio distribution across `agentdeck/` measured and reported before the threshold is chosen |
| Check A fires correctly | a touched file whose overall ratio exceeds threshold fails; an untouched file with the same ratio does not |
| Check B fires correctly | a diff whose added lines are disproportionately comments fails even inside a large, otherwise-compliant file |
| Free sections still count here | a file passing SLOP010 (via a long exempt `Args:` section) still counts those lines toward this ratio; confirm the two rules' predicates are deliberately different, not a copy-paste of SLOP010's exemption |
| No new false positives on the existing suite | `make check` green, including the self-test fixtures this rule adds |

`make check` in the foreground with `< /dev/null`, output pasted.
