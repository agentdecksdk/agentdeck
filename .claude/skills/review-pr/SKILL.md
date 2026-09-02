---
name: review-pr
description: Review gate for one agentdeck PR before merge. Five phases (claims, attack, craft, scope, verdict) plus an optional harvest; BLOCK/DISCUSS/DEFER/NIT findings; a 300-word verdict ending in a greppable marker. Use before merging any agentdeck PR.
---

# Review a PR

A reviewer finds what matters, says it, and stays silent elsewhere. Prove nothing; assert what you found.

## Setup

1. Read `CLAUDE.md`, `docs/engineering/` (`principles.md`, `coding-standards.md`, `coding-agents.md`, relevant specialized standards), and the `docs/patterns/` files for the concerns this PR touches.
2. Read the linked issue (`gh issue view <n>`) and the full diff (`gh pr diff <n>`).
3. Check out the branch (`gh pr checkout <n>`), seed the worktree (copy `.env` if present, then `uv venv --python 3.12 && make install`), run `make check`. It ends by naming the docs-site pages this diff affects: open every page the PR left unchanged and confirm it is still true. No CI job does this; it is where stale prose is caught (#409 shipped five stale references past a ticked box).
4. Run `uv run scripts/quality_delta.py` and `PR_BODY="$(gh pr view <n> --json body -q .body)" uv run scripts/concept_budget.py`. Hold the PR to the issue's `## Expected delta` and the PR's `## Concept budget`. A declaration written after the code is not a declaration, so where the two disagree the issue's number binds. Unexplained overrun beyond roughly 2x runs the overrun pass (`references/overrun.md`); the number itself is never the finding.
5. **Sibling comparison:** for each changed or new module, pick 2-3 canonical siblings from `uv run scripts/repomap.py` (same package, same responsibility class), read them, and verify the PR's own `## Analog` claim against the code.

## Phases

Work in order. Correctness before craft: a wrong design makes naming irrelevant. Copy this checklist into your own response and check off each phase as you complete it:

```
Review progress:
- [ ] 1. Claims: what does this PR assert, and what must be true
- [ ] 2. Attack: go after those claims (references/attack.md)
- [ ] 3. Craft: patterns, naming, tests as spec (references/craft.md)
- [ ] 4. Scope: should this be two PRs
- [ ] 5. Verdict: post it (references/templates.md)
- [ ] 6. Harvest: usually nothing (references/craft.md)
```

| phase | does | writes on the PR |
|---|---|---|
| 1. Claims | Two lines: what this PR asserts, what must be true for it to hold. | nothing |
| 2. Attack | Go after those claims: lifecycle, event ordering, concurrency, failure paths, states the code cannot express. Edge cases belong here, not later; mutation-test anything the PR calls covered. Depth: `references/attack.md`. | inline (Template A) |
| 3. Craft | Does it read like its neighbors: `docs/patterns/`, naming, error text, comment slop, test names that state contracts, the PR body's own verbosity. Depth: `references/craft.md`. | inline (Template A) |
| 4. Scope | Should this be two PRs? Is anything here not asked for? An unexplained overrun against the issue's estimate runs the overrun pass, which returns the design cause and the smaller shape, not a verdict on the number: `references/overrun.md`. | one line, or the cause the overrun pass named |
| 5. Verdict | Draft the rubric row per phase (Claims/Attack/Craft/Scope) and findings by class, format fixed in `references/templates.md` (Template B). Before posting, re-read each finding and ask whether it names a concrete consequence; downgrade it to NIT if it does not. Only then post. | 300 words max, hard cap |
| 6. Harvest | Optional, non-blocking, gated: see below. The default is to write nothing. | an issue carrying the entry text, never a commit and never on the PR |

## Harvest

**The default is to write nothing.** Most reviews produce neither output below. `docs/patterns/` is 106 lines total across four files; that shortness is what makes it worth reading, and a reviewer appending once per PR would double it in a week. Pattern entries are not scored by volume: the measure is whether a later review cites one, never how many exist. Full bar and rationale: `references/craft.md`.

- **A `docs/patterns/` entry**, only when all four of `references/craft.md`'s tests pass: it recurred, it generalizes past this module, it is a real cited good/bad pair, and it is not already covered by the four existing files. Extending an existing file beats creating one. You never commit it: open an issue containing the finished entry text and the file it belongs in, and a dev agent lands it. A reviewer that commits has reviewed nothing, and taste belongs under review like any other change.
- **A harness note**, only when the same dev-agent *behavior* (not output) has appeared in two PRs, both cited. File it the same way as a DEFER (`finding: <gap>` issue) but name the behavior, not a proposed rule. Not a new mechanical guard: guards caught 0 of 8 v5 findings, and three of those findings were themselves guard proposals.

Anything that surfaces but fails its bar is a NIT, or it is nothing. It is never a pattern.

## Finding classes

BLOCK / DISCUSS / DEFER / NIT replace ERROR / WARNING / NOTE. Each is defined by what happens next, not by severity. Worked examples: `references/classify.md`.

| class | means | resolved by | blocks |
|---|---|---|---|
| BLOCK | a defect with a named consequence, introduced by this PR | author fixes it here | yes |
| DISCUSS | reviewer is genuinely unsure, or the call is above a reviewer's authority | author answers in thread; unresolved disagreement escalates to the user | not once answered |
| DEFER | real, but not this PR's job, whether or not it is actionable yet | reviewer files the issue and links it | no |
| NIT | small, cheap, optional | nobody, unless the author wants to | no |

- DISCUSS resolves when the author *answers*, not when the author agrees. Disagreement after an answer goes to the user; neither side rules.
- A DISCUSS must state what would settle it. A shrug is not a DISCUSS.
- Anything touching public API shape is a DISCUSS, never a unilateral BLOCK.
- A NIT needs no reply. Collect NITs in one list at the bottom of the verdict, never inline.
- A design cause the overrun pass names blocks like any other BLOCK: the consequence is the code the wrong shape produced, and the fix is the smaller design. Splitting a PR whose size came from a missing concept moves the bloat rather than removing it.
- Cost is folded into the class, not a separate axis. Real, expensive, and not a regression is a DEFER; introduced by this PR is a BLOCK regardless of cost.
- DEFER is not a rejection and does not require present actionability: a finding that only informs a later bug fix or feature is still worth filing. NIT means small and optional now; DEFER means real and later.
- DEFER is where the promotion loop lives: file `finding: <gap>` with the `finding` label for a real out-of-scope defect, citing evidence. Never file a DEFER proposing a new mechanical guard; guards caught 0 of 8 v5 findings.

## Report format

Every comment and the verdict body follow fixed templates: `references/templates.md`. Do not improvise structure, length, or style; it is all decided there, including the exact marker line.

## Delivering the review

- Reviews are COMMENTED only. Approve and request-changes are unavailable when the agent authenticates as the PR author.
- Inline, line-anchored comments (BLOCK, DISCUSS, DEFER; NITs never inline) in Template A form:
  `gh api repos/{owner}/{repo}/pulls/<n>/comments -f body=... -f commit_id=$(gh pr view <n> --json headRefOid -q .headRefOid) -f path=<file> -F line=<line> -f side=RIGHT`
- Post the verdict as a real review in Template B form: `gh pr review <n> --comment --body-file <file>`. Pass requires zero BLOCK and every DISCUSS answered.
- Post measured against declared with the verdict, one line, so the author sees the delta they estimated. The raw script output stays in the return to the orchestrator.
- The review is only delivered once it is on the PR. A local pass with nothing posted is incomplete.
- A push that changes the code needs a new review: nothing enforces that mechanically, so it is on whoever merges.
- File DEFER and harness-note issues per the finding-class table above.

## Return to the orchestrator

Template C form (`references/templates.md`): verdict, `make check` result, counts per finding class, links to the posted review and any issues filed, then the evidence, uncapped, raw, no prose (mutation results, measured delta vs the PR's declarations, sibling-comparison notes, probe limits, any `docs/patterns/` commit).
