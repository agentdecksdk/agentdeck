---
name: ship-issue
description: "Run one agentdeck issue through the full pipeline: brief the user, deck-dev implements it, deck-reviewer gates it, findings get fixed, PR merges to dev, board updated."
---

# Ship an issue

Orchestrate the end-to-end delivery of an issue.

## 0. Brief Before Launching
Read the issue and verify its claims against the tree. **Spec gate first:** if the issue lacks `Done when` outcomes or scope bounds (what must NOT be added), fix the issue body or ask the user; do not spawn deck-dev against an unbounded spec. Then output a concise 5-point brief (1–2 lines each):
> **The claim:** What the issue asks for.
> **What's true now:** Current behavior in the codebase (file:line).
> **The shape:** Implementation plan in 3–4 concise bullets.
> **The hard gate:** Invariants that must not break (`tests/golden/`, public API, import law).
> **Skipped:** What is deliberately out of scope.

## 1. Board Tracking
Update GitHub Project (`PVT_kwHOBHijkM4BgHFZ`): Set **Status = In progress**, record Start Date, comment with start timestamp.

## 2. Implement
Spawn `deck-dev` with the issue number and brief. It runs Stage 0 (Understand) then Stage 1
(Design, posted to the draft PR body), then stops there and reports back rather than continuing
into Stage 2.

If it stops at its own spec gate despite step 0, the issue needs a human decision: set the board
back to **Todo**, surface what is missing, stop the pipeline.

**Design gate:** compare the reported design against this step's own brief. A design is never
blocked for being larger; it is blocked only for a *named* problem: a duplication of something
already in `scripts/repomap.py`'s output, an abstraction with one caller, a divergence from its
own declared `## Analog`, configuration for a value that never changes. No such problem: send
`deck-dev` a message to proceed to Stage 2. A problem found: send it back naming the problem, not
a target size; `deck-dev` revises Stage 1 and reports again before Stage 2 starts.

Once past the gate, `deck-dev` continues through Implement, Self-review, and Gate, posting task
progress; poll for stalls.

## 3. Attach PR
Verify the draft PR targets `dev`, includes `Closes #<n>`, and its body carries the design sections (`## Reuse analysis`, `## Analog`, `## Concept budget`, `## Expected delta`). Missing sections go back to deck-dev before any review is spent. Set **Status = In review** when marked ready.

## 4. Review
Spawn `deck-reviewer` on the PR. The reviewer must post a COMMENTED GitHub review; a session-only verdict does not count.

## 5. Address Findings
The review lands on the PR itself (GitHub review + inline comments); read it there. Route by class: **BLOCK** goes to `deck-dev` to fix on the branch. **DISCUSS** is answered in the thread by the author; if the reviewer and author still disagree once answered, escalate to the user rather than letting either side rule. **DEFER** is already a `finding:`-titled issue the reviewer filed and linked; schedule it as its own harness PR, never implemented by the reviewer. **NIT** needs no action unless the author wants it.

## 6. Merge
Merge when every required check is green **and every review thread is resolved**: `gh pr merge --squash --delete-branch`.

Green checks are not sufficient. `dev`'s ruleset sets `required_review_thread_resolution`, so one open thread holds the PR at `BLOCKED` with nothing in `gh pr checks` to explain it, and `--admin` will not force it past `enforce_admins: true`. When a merge is refused and the checks look fine, read the ruleset rather than the protection endpoint, which does not report this:

```bash
gh api repos/{owner}/{repo}/rules/branches/dev
gh api graphql -f query='{repository(owner:"{owner}",name:"{repo}"){pullRequest(number:<n>){reviewThreads(first:20){nodes{id isResolved}}}}}'
```

## 7. Complete
Set **Status = Done**, record Target Date, comment with completion summary and PR link.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
