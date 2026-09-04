---
name: milestone-retro
description: Evaluate the harness after a milestone closes: which CI gates fired, whether the slop guards caught anything, how the dev and review agents performed, and whether the PRs were well shaped. Judges the machinery, not the product. Use when a milestone is finished and before the release is tagged.
---

# Milestone retro

Judge the machinery: the gates, the guards, the two agents, the shape of the PRs. **Not the product.** Whether the features were right is a different question with a different audience.

Every claim in a retro is a number with a query behind it. An opinion about the harness that nobody measured is how the harness got this way.

## Setup

The milestone is the set of PRs merged to `dev` between two tags, or the PRs closing one milestone's issues. Fix that set first and write it down; every phase below is scoped to it.

```bash
gh pr list --state merged --limit 200 --json number,title,mergedAt,additions,deletions,changedFiles \
  --jq '.[] | select(.mergedAt > "<start>") | "\(.number)\t\(.mergedAt)\t+\(.additions)/-\(.deletions)\t\(.changedFiles)f\t\(.title)"'
```

Where `scripts/agent_scorecard.py` has already recorded a PR, read its record instead of recomputing. This skill covers what the scorecard cannot: the gates, the guards, and judgment.

## Phases

Work in order. Copy this checklist into your own response and check off each phase:

```
Retro progress:
- [ ] 1. Gates: which CI checks fired, which never did (references/queries.md)
- [ ] 2. Guards: what the slop rules actually caught (references/queries.md)
- [ ] 3. Agents: dev and reviewer, measured (references/queries.md)
- [ ] 4. PRs: bodies, commits, comment routing (references/queries.md)
- [ ] 5. Criteria: audit the scorecards themselves (references/judging.md)
- [ ] 6. Verdict: file the issues (references/judging.md)
```

| phase | asks | output |
|---|---|---|
| 1. Gates | Which required checks failed, and what did each catch? A gate that never fails and a gate that always fails are both broken. | a table: gate, runs, failures |
| 2. Guards | Did any mechanical guard catch a defect this milestone, or only formatting? Compare against the findings filed. | guard hits against real findings |
| 3. Agents | Dev: defect at first review, rework ratio, scope creep. Reviewer: escapes, rounds, loop rounds, words per finding. | two scorecards, never merged into one |
| 4. PRs | Body size against the cap, commit hygiene, whether findings were answered in threads or by growing the body. | per-PR table |
| 5. Criteria | Are the scorecards measuring the right things? Which metric changed a decision, which rewards the artifact rather than the value, which can be gamed, which cost is unmeasured. The criteria are under review here, not the agents. | metrics kept, killed, added |
| 6. Verdict | What cost us time, ranked. Each gets an issue or it did not matter. | filed issues |

Phase 5 runs after phase 3 and not before, because a criterion is judged on whether it earned its keep this milestone, and you cannot know that until you have used it. It **proposes**: criteria are policy, and the user rules on keep or kill. Two of its four questions belong at proposal time instead, in the issue that adds a metric; `references/judging.md` says which and why.

## Rules

**CONSTRAINT: every claim carries its query.** A retro that says a gate is noisy without the failure count is an opinion. If you cannot produce the number, say the number is unavailable and why; never estimate it.

**CONSTRAINT: no agent's own report is evidence.** The dev agent declared done on 5 of 5 defective PRs in v5.0.0. Read GitHub, read git, read the diff. An agent's summary is a claim to verify, not a source.

**CONSTRAINT: separate the dev and reviewer scorecards.** Their jobs are opposite: one produces, one detects. A shared metric set measures neither. See `references/judging.md`.

**EXPECTATION: report what went well with the same evidence.** A retro that only finds faults will get the working parts cut. In v5.0.0 the reviewer caught 8 of 8 real defects and the guards caught 0, and the naive conclusion from a fault-only retro would have been to add more guards.

**EXPECTATION: name what you could not measure.** Escape rate is lagging and only resolves weeks later. Agent token and duration figures live in the completion notification and are gone once the session ends. Say so rather than leaving a gap that reads as a zero.

**JUDGMENT: how deep to go.** Default to the five phases above. A milestone under three PRs does not need phase 4.

## Out of scope

- **Product correctness.** Whether the features were the right features, whether the API is good, whether the docs read well. Different question, different retro.
- **Rewriting the harness in this session.** The retro produces issues. Implementing them is the normal pipeline, and mixing the two means the measurement stops when the first fix starts.
- **Any metric counted by volume.** Entries written, findings raised, tests added. Volume metrics manufacture volume. `references/judging.md` covers what to use instead.
