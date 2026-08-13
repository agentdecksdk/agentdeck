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

## If it is labelled `good first issue`

The reader arrives from GitHub issue search knowing nothing about AgentDeck, so the issue has
to give them a reason to run it — a contributor who never runs the SDK makes a text edit and
leaves. Append this block verbatim, with `<example>` replaced by whichever directory in
`examples/` sits closest to the issue's subject:

```markdown

---

### New to AgentDeck?

AgentDeck SDK is a production runtime around agents you already have — it supplies sessions,
streaming, one event log per run, human approval and run control, and leaves execution to the
OpenAI Agents SDK and LangGraph.

```bash
pip install agentdeck-sdk
```

**Before working on this issue, run this example as a user would** — `python run.py` in
[`examples/<example>`](https://github.com/agentdecksdk/agentdeck/tree/dev/examples/<example>).
Fifteen minutes there makes this issue read very differently.

Setup, the `make check` gate, the branch model (`dev`) and the CHANGELOG rule are in
[CONTRIBUTING.md](https://github.com/agentdecksdk/agentdeck/blob/dev/CONTRIBUTING.md). Comment
here to claim the issue — nobody else gets assigned while you are working on it, and questions
are welcome in [Discussions](https://github.com/agentdecksdk/agentdeck/discussions).
```

Keep the pool at 5–10 open, each finishable in 30 minutes to 3 hours, and prefer work that
forces the contributor to run the SDK over pure text maintenance. Anything well-defined but
larger gets `help wanted` instead. Never manufacture an easy issue to fill the pool — the
policy and its reasoning are in `docs/delivery/plan-adoption.md` §9.
