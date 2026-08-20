---
name: ship-issue
description: Run one agentdeck issue through the full pipeline  -  brief the user, deck-dev implements it, deck-reviewer gates it, findings get fixed, PR merges to dev, board updated.
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
Spawn `deck-dev` with the issue number and brief. It works in stages (understand, design-in-PR-body, implement, self-review, gate) and posts task progress; poll for stalls.
If it stops at its own spec gate despite step 0, the issue needs a human decision: set the board back to **Todo**, surface what is missing, stop the pipeline.

## 3. Attach PR
Verify the draft PR targets `dev`, includes `Closes #<n>`, and its body carries the design sections (`## Reuse analysis`, `## Analog`, `## Concept budget`, `## Expected delta`). Missing sections go back to deck-dev before any review is spent. Set **Status = In review** when marked ready.

## 4. Review
Spawn `deck-reviewer` on the PR.

## 5. Address Findings
The review lands on the PR itself (GitHub review + inline comments); read it there. Route by class: **ERROR** and **WARNING** findings go to `deck-dev` to fix on the branch; **NOTE** findings that will not be fixed now become `finding:`-titled issues via open-issue, never silently dropped. Promotion issues the reviewer filed get scheduled as their own small harness PRs, never implemented by the reviewer.

## 6. Merge
When approved and `make check` is green: `gh pr merge --squash --delete-branch`.

## 7. Complete
Set **Status = Done**, record Target Date, comment with completion summary and PR link.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
