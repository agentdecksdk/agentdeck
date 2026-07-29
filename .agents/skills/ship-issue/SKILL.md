---
name: ship-issue
description: Run one agentdeck issue through the full pipeline — deck-dev implements it, deck-reviewer gates it, findings get fixed, PR merges to dev. Use when the user says "ship issue N", "handle issue N", or wants an issue implemented and merged end-to-end.
---

# Ship an issue

Orchestrate; don't implement inline. The pipeline:

1. **Implement.** Spawn `deck-dev` with the issue number (background). If several independent issues were requested, spawn one agent each, in parallel.
2. **Review.** When the PR opens, spawn `deck-reviewer` on it (background).
3. **Fix findings.** On "request changes", send the findings back to the same deck-dev agent (SendMessage — it keeps its worktree and branch) to fix and push to the existing PR. Skip findings the reviewer marked non-blocking. Trivial nits (a comment line, a missing timeout) you may fix and push yourself instead.
4. **Merge.** On approve (or after fixes): verify `gh pr checks` is green and the PR is MERGEABLE. If CONFLICTING, merge origin/dev into the branch — normal merge, never force-push (it's blocked); CHANGELOG conflicts resolve as a union keeping both sides. Then `gh pr merge --squash --delete-branch`.
5. **Report.** PR URL, what shipped, review verdict, anything deferred.

Ground rules:
- CI is the gate of record — a machine-local ty/venv failure that CI doesn't reproduce is not a blocker (this machine's shared venv is known-flaky).
- Never merge a PR whose review found unaddressed blocking findings.
- Agents idle mid-task ("running X in the background…") get one SendMessage nudge to continue.
- Relay each stage's outcome to the user as it happens; don't go silent until the end.
