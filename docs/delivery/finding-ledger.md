# Finding ledger

**Status:** Design of record, append-only.

One row per BLOCK and DISCUSS, written by `deck-reviewer` at verdict time (#697). It exists so the
next review can count what the last one saw: `review-pr`'s Harvest bar asks for a behavior seen in
two PRs, and nothing recorded the first.

**The `behavior tag` names the dev-agent behavior, not the defect.** `assertion-cannot-fail`, not
`missing test`. Reuse an existing tag whenever one fits: a tag invented per row makes the ledger
write-only, since the only thing a ledger is for is the repeat count.

Read it with `uv run scripts/finding_ledger.py`, which prints the tag histogram and names any tag
at three rows or more. Counting is mechanical; deciding what a repeat means is `deck-insight`'s job.

**Not a metric.** Rows going up is not progress. The only score is a refinement a later review
cites. Never backfill: this file starts empty and earns its rows.

**Era matters.** The BLOCK bar moved twice during v6 (#609, #615), so a row records the era it was
written under. Do not read a trend across an era boundary.

## Era: post-#615

| PR | site | class | behavior tag |
|---|---|---|---|
