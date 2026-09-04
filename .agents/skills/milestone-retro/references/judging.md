# Reading the numbers

Contents: what makes a metric honest, the two scorecards, auditing the criteria, what a finding must clear, worked examples from v5.0.0.

## What makes a metric honest

**No agent grades itself.** The dev agent's score is the reviewer's BLOCK count at first review. The reviewer's score is what escaped it, which only later findings reveal. Everything else comes from GitHub, git, and the agent completion record. A self-reported number is a claim, not evidence.

**Every metric needs a counterweight, or it gets gamed in one direction.** Escape rate alone produces a reviewer that blocks everything, so it is read against rounds and words. BLOCK count alone produces a dev agent that ships trivially small PRs, so it is read against scope creep. If a metric has no counterweight, do not ship it.

**Never count by volume.** Patterns entries written, findings raised, tests added. A metric that wants a number up manufactures that number. Measure use instead: a patterns entry counts when a later review cites it, not when it is written.

**Size is a trigger, never a finding.** A diff larger than its shape suggests means look harder at reuse and pattern conformance. What blocks is always a named duplication, a one-caller abstraction, a divergence from the declared analog, or configuration for a value that never changes. "Too big" is uncitable and an author wins that argument by compressing badly. Clean code is smaller as a consequence, so judge the cause.

**Declared against actual is a ceiling, not accuracy.** `concept_budget.py` fails the build when actuals exceed the declaration, and that is all it does. Hitting your own number is not an achievement.

## The two scorecards

They stay separate because the jobs are opposite: one produces, one detects.

**Dev agent, "was it correct when it said done":**

| metric | direction |
|---|---|
| BLOCK count at first review | down |
| rework ratio, commits after `ready_for_review` | down |
| reuse and pattern BLOCKs | down |
| scope creep, files touched against files the issue named | down |
| `make check` green before `ready` | always |

**Reviewer, "did it find what was there, at what cost":**

| group | metric | direction |
|---|---|---|
| recall | escape rate | down |
| value | outcome rate: findings that produced a code change, a picked-up DEFER, or a patterns entry | up |
| value | words per finding | down |
| value | percent anchored inline rather than in the verdict body | up |
| speed | total latency across rounds | down |
| speed | productive rounds | fast, not few |
| waste | loop rounds | to zero |
| learning | patterns entries a later review cited | up |

Only a finding that produced **literally nothing** counts against outcome rate. A DEFER that informs a later fix is a legitimate outcome; it is not a rejection.

## Auditing the criteria (phase 5)

The scorecards are under review here, not the agents. A metric set nobody audits ossifies, and the wrong metric is worse than no metric because it directs work.

Four questions, and they do not all belong here. Two are design questions answerable the moment a metric is proposed; two need a milestone of use behind them.

**Asked when a metric is proposed, in the issue that adds it, never deferred to a retro:**

2. **Can it be gamed in one direction, and does its counterweight still hold?** Name the gaming move and the metric that catches it. A metric whose counterweight you cannot name is not shippable.
3. **Does it reward the artifact or the value?** Anything counted by volume rewards the artifact. So does any metric scoring an agent against its own declaration.

Both metrics that failed in v5.0.0 failed question 3, and both were catchable at proposal. Asking these late costs a milestone of work pointed the wrong way.

**Asked here, because they need usage:**

1. **Did it change a decision this milestone?** Name the decision. A metric computed, read, and acted on nowhere is dead weight.
4. **Is it actually obtainable?** A metric that lags by weeks, or whose source vanishes with a session, is not wrong but cannot score a PR at merge. Say which are live and which are retrospective rather than reporting a gap as a zero.

Then the question the four cannot ask: **what cost the milestone real time that no metric saw?** That gap is a finding, and it is usually the most valuable output of this phase.

### Who rules

**The retro proposes; the user decides.** Criteria are policy, not fact, and the agent running the retro may have authored the metrics it is auditing. Present the evidence, recommend keep or kill for each, and stop. Same class as a DISCUSS finding: neither agent rules.

One thing is decidable without judgment, and it is the backstop for criteria nobody gets round to auditing: **a metric that changed zero decisions across two consecutive milestones is flagged for deletion automatically.** State it as a fact in the output, not as a proposal.

The two v5.0.0 kills both came from a person challenging a metric out loud, which is not a mechanism. This section is the mechanism.

Two metrics failed this audit in v5.0.0, both proposed during the retro that created these scorecards:

| metric | why it failed | replaced by |
|---|---|---|
| declared against actual LOC, as accuracy | rewards padding: declare +1000, ship +1000, score "exact". Question 3 | reuse and pattern BLOCKs at first review, which measures the cause instead of the symptom |
| patterns entries per milestone, direction up | manufactures the spam the entry bar exists to prevent. Question 3 | entries a later review cited |

Both survived until someone challenged them out loud. That is the reason this phase exists rather than being left to whoever notices.

## What a finding must clear to be filed

A retro that files everything it noticed produces a backlog nobody works. Each filed issue needs:

1. **Evidence.** A number, a query, or a quoted failure. Not an impression.
2. **A cost.** What it took from the milestone: a wasted hour, a re-review round, a shipped defect, a check nobody reads. If you cannot name the cost, it did not matter enough to file.
3. **A bound.** `Done when` outcomes and an explicit `Not in scope`, because the fix is a normal pipeline PR and an unbounded spec produces unbounded code.

Rank by cost before filing. A retro that files ten equal-weight issues has ranked nothing.

## Worked examples from v5.0.0

**A gate that never fires.** The docs build ran 23 times and failed 0, because roughly 21 of those PRs touched no file under `docs-site/`. Not broken, just never asked a question that could have a "no". Fix was a changed-files check, not deletion, because a required check reporting *skipped* does not satisfy branch protection.

**A gate that fires constantly and catches nothing.** Docs-impact failed 19 of 42 runs, because its acknowledgement expired on every push rather than when a new page became impacted. The box then got re-ticked unread, and #409 shipped five stale prose references past a green check. Both halves are findings: the noise, and the fact that the noise made the gate useless.

**Guards against reality.** Eight `finding:` issues were filed during the milestone. Zero could have been caught by any of the eight slop rules; every one was semantic (lifecycle ordering, event ordering, a dead field, a docstring asserting a guarantee the code did not make). Three of the eight findings were themselves proposals for new guards. The conclusion is not "add guards".

**What went well, measured the same way.** The reviewer caught 8 of 8 real defects, mutation-tested its own verdicts rather than reading tests, and once talked itself out of filing an issue with an argument from the code. A fault-only retro would have proposed thinning it.

**A number that was wrong until checked twice.** The dev agent's rework ratio read as 100% on one PR until `authoredDate` replaced `committedDate`; a rebase had rewritten every timestamp. Check any metric that suddenly reads as an extreme.
