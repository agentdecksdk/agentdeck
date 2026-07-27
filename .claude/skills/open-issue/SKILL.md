---
name: open-issue
description: File a well-formed agentdeck GitHub issue in the repo's house style (Problem / Proposed shape / Notes / Done when). Use when the user asks to open, file, or create an issue, or to turn an idea/bug into an issue.
---

# Open an agentdeck issue

Issues here are implementation-ready specs — a subagent should be able to build from one without asking questions. Match the house style of existing issues (`gh issue list`, read one or two for tone).

Structure (omit a section only when truly empty):

```markdown
## Problem
What's impossible or broken today, and why it matters for Middle/agentdeck. For bugs: the exact traceback and a minimal repro command.

## Proposed shape
The concrete API/behavior — real code snippets of the intended usage, named modules/functions, error behavior. State what stays unchanged. Prefer the minimal design; call out what is deliberately out of scope.

## Notes
Constraints, interactions with other issues/PRs (link them), which existing patterns to reuse (name the file).

## Done when
A checklist of observable behaviors, each phrased so it maps 1:1 to a test (no live model calls). This is the review gate's contract.
```

Rules:
- Title: terse and specific — `<area>: <what>` (e.g. "spawn_subagent: stream nested subagent deltas").
- One issue per concern; split unrelated asks.
- Check for duplicates first (`gh issue list --state open`).
- File with `gh issue create -t <title> -b <body>` and return the URL.
