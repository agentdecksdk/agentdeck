---
name: ship-issue
description: Run one agentdeck issue through the full pipeline — brief the user, deck-dev implements it, deck-reviewer gates it, findings get fixed, PR merges to dev, board updated.
---

# Ship an issue

Orchestrate the end-to-end delivery of an issue.

## 0. Brief Before Launching
Read the issue and verify its claims against the tree. Output a concise 5-point brief (1–2 lines each):
> **The claim:** What the issue asks for.
> **What's true now:** Current behavior in the codebase (file:line).
> **The shape:** Implementation plan in 3–4 concise bullets.
> **The hard gate:** Invariants that must not break (`tests/golden/`, public API, import law).
> **Skipped:** What is deliberately out of scope.

## 1. Board Tracking
Update GitHub Project (`PVT_kwHOBHijkM4BgHFZ`): Set **Status = In progress**, record Start Date, comment with start timestamp.

## 2. Implement
Spawn `deck-dev` with the issue number and brief.

## 3. Attach PR
Verify draft PR targets `dev` and includes `Closes #<n>`. Set **Status = In review** when marked ready.

## 4. Review
Spawn `deck-reviewer` on the PR.

## 5. Address Findings
Route blocking findings to `deck-dev` to fix on the branch.

## 6. Merge
When approved and `make check` is green: `gh pr merge --squash --delete-branch`.

## 7. Complete
Set **Status = Done**, record Target Date, comment with completion summary and PR link.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
