---
name: ship-issue
description: Run one agentdeck issue through the full pipeline — brief the user, deck-dev implements it, deck-reviewer gates it, findings get fixed, PR merges to dev, board updated. Use when the user says "ship issue N", "handle issue N", "start issue N", or wants an issue implemented and merged end-to-end.
---

# Ship an issue

Orchestrate; don't implement inline. Steps 0–7 run without being asked for.

## 0. Brief before launching

Read the issue, then check its claims against the tree — issues here routinely predate the
code they describe. Post the brief as five headlines, one to four lines each, nothing else:

> **The claim** — what the issue asks for, one line.
> **What's true now** — what the tree actually does; name every stale premise, with file:line.
> **The shape** — the plan in 3–5 bullets, files named.
> **The hard gate** — what must not move: `tests/golden/` snapshots, the frozen v1 wire, the
>   public API, import law.
> **Skipped** — what we deliberately won't build, and the trigger that would change that.

Then launch. Only stop for approval when the correction changes what the issue is *for* —
say which headline turned, in one line, and wait.

## 1. Open the work on the board

Project `PVT_kwHOBHijkM4BgHFZ` (users/sagi5060/projects/5) — `gh project item-edit`:

| field | id | values |
|---|---|---|
| Status | `PVTSSF_lAHOBHijkM4BgHFZzhaVHh8` | In progress `47fc9ee4` · In review `78b3efd5` · Done `98236657` |
| Start date | `PVTF_lAHOBHijkM4BgHFZzhaVHnE` | `--date YYYY-MM-DD` |
| Target date | `PVTF_lAHOBHijkM4BgHFZzhaVHnI` | end date, set at merge |

Set **Status = In progress**, fill **Start date**, and comment on the issue with the UTC start
time (`date -u`) plus any scope correction from the brief — the date field is date-only, so the
clock time and the reasoning only survive in the comment.

## 2. Implement

Spawn `deck-dev` with the issue number and the brief's corrected scope (background). Several
independent issues → one agent each, in parallel.

## 3. Attach the PR

When the draft opens, verify it targets `dev` and its body says `Closes #<n>` — that link is what
fills the board's *Linked pull requests* column. Fix it yourself if either is wrong.
When deck-dev flips it ready, set **Status = In review**.

## 4. Review

Spawn `deck-reviewer` on the PR (background).

## 5. Fix findings

On "request changes", send the findings back to the same deck-dev agent (SendMessage — it keeps
its worktree and branch) to fix and push to the existing PR. Skip findings the reviewer marked
non-blocking. Trivial nits (a comment line, a missing timeout) you may fix and push yourself.

## 6. Merge

On approve (or after fixes): verify `gh pr checks` is green and the PR is MERGEABLE. If
CONFLICTING, merge origin/dev into the branch — normal merge, never force-push (it's blocked);
CHANGELOG conflicts resolve as a union keeping both sides. Then
`gh pr merge --squash --delete-branch`.

## 7. Close the work on the board

Set **Status = Done** and **Target date** to the merge date, and comment on the issue with the
UTC end time and the elapsed wall clock. Then report: PR URL, what shipped, review verdict,
anything deferred.

## Ground rules

- CI is the gate of record — a machine-local ty/venv failure CI doesn't reproduce is not a
  blocker (this machine's shared venv is known-flaky).
- Never merge a PR whose review found unaddressed blocking findings.
- Agents idle mid-task ("running X in the background…") get one SendMessage nudge to continue.
- Relay each stage's outcome as it happens; don't go silent until the end.
