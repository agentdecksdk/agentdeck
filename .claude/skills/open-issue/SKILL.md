---
name: open-issue
description: File a well-formed agentdeck GitHub issue in the repo's house style (Problem / Proposed shape / Notes / Done when).
---

# Open an agentdeck issue

Issues are implementation-ready specifications. Be concise, precise, and dense with signal.

## Issue Structure

```markdown
## Problem
What is broken or missing, and why it matters. For bugs: exact traceback and minimal reproduction steps.

## Proposed shape
The clean, user-intent API or behavior with minimal code snippets. Show the simplest path with zero leaked plumbing. Explicitly state what is out of scope.

## Notes
Constraints, affected modules, existing patterns to follow, related issues/PRs.

## Pitfalls
Concrete ways this goes wrong, one line each, every one tied to a ruling, a file:line, or a past incident. A reviewer reads this list against the diff.

## Must not
Scope bounds a reviewer can block on: what this change must not add, touch, or decide.

## Done when
Checklist of observable, testable behaviors (mapping 1:1 to deterministic tests without live model calls).
```

## Rules
- **Anti-verbosity:** Avoid fluff and rambling rationale. One clear sentence beats a paragraph.
- **Title:** Terse and specific: `<area>: <what>` (e.g., `runtime: propagate cancellation to subagents`).
- **Single concern:** One issue per topic; split unrelated asks.
- **Check duplicates:** `gh issue list --state open`.
- **Labels:** Select one type label (`bug`, `enhancement`, `feature`, `chore`, `design`, `documentation`, or `finding`) and the closest `area:*` label from `gh label list`.
- **Output limits:** Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
- Create via `gh issue create -t <title> -b <body> --label <type> --label <area>` and verify the resulting issue has both labels before returning its URL.
