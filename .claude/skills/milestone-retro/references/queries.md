# Retro queries

Contents: traps that produce wrong numbers, gates, guards, agents (dev and reviewer), PR shape.

## Traps that produce wrong numbers

Read these first. Each one silently returned a wrong answer during the v5.0.0 retro.

| trap | what happens | fix |
|---|---|---|
| `gh pr list` default limit | returns 30, or whatever you pass. Querying 60 against a 198-PR repo made 19 branches read as "no PR" and nearly deleted them as dead | always pass `--limit 500` and check the returned count |
| `committedDate` after a rebase | every commit's timestamp is rewritten, so a rebased PR reads as 100% rework | use `authoredDate` for anything comparing commits to an event |
| a conflicting PR looks like broken CI | it gets **zero** `pull_request` runs because there is no merge ref, while `pull_request_target` jobs keep firing. Reads as "CI partially vanished", not "you have a conflict" | `gh pr view <n> --json mergeable` first, always |
| `--log-failed` on a multi-job run | mixes jobs, so one job's failure appears under another's name | resolve the job id, then `gh run view --job <id> --log` |
| grep for a rule id in a script | finds the definition and its self-test, not its firings | read the CI job's log for what it actually flagged |

## 1. Gates

Runs and failures per workflow, scoped to the milestone:

```bash
gh run list --limit 200 --json name,conclusion,createdAt \
  --jq '[.[] | select(.createdAt > "<start>")] | group_by(.name)
        | map({name: .[0].name, total: length,
               fail: ([.[] | select(.conclusion=="failure")] | length)})
        | sort_by(-.total) | .[] | "\(.name)\ttotal=\(.total)\tfail=\(.fail)"'
```

Both extremes are findings. A gate at 0% has never asked a question that could have a "no". A gate above roughly 30% is either badly shaped or is a lint rule wearing a CI job's clothes, and it trains everyone to ignore it.

For any gate that failed, read one failure and record **what it caught**, not that it fired. A gate can fail often and catch nothing.

Per-job conclusions inside a multi-job workflow:

```bash
for id in $(gh run list --workflow=<file>.yml --limit 100 --json databaseId,createdAt \
              --jq '.[]|select(.createdAt>"<start>")|.databaseId'); do
  gh run view $id --json jobs --jq '.jobs[]|select(.name=="<job>")|.conclusion'
done | sort | uniq -c
```

## 2. Guards

What the anti-slop gate actually flagged:

```bash
jid=$(gh run view <run-id> --json jobs --jq '.jobs[]|select(.name=="slop")|.databaseId')
gh run view --job "$jid" --log | grep -E "SLOP[0-9]"
```

Then the question that matters: **cross-reference guard hits against the findings filed during the milestone.** List every `finding:` issue and ask, for each, which guard rule could have caught it. In v5.0.0 the answer was none, for all eight.

```bash
gh issue list --label finding --state all --limit 100 --json number,title,createdAt \
  --jq '.[] | select(.createdAt > "<start>") | "\(.number)\t\(.title)"'
```

Note how many of those findings are themselves requests for new guards. That ratio is the tell.

A guard's silence in CI is not proof it is useless: the write-time hook may be catching things where nothing logs. Say that rather than concluding from CI alone, and fix the observability before proposing a deletion.

Rules still violated in tree, for a guard that only checks added lines:

```bash
git grep -c '<pattern>' -- . | sort -t: -k2 -rn
```

An exemplar file that breaks the rule it teaches is a finding on its own.

## 3. Agents: dev

Rework, per PR. This is the honest "was it done when it said done":

```bash
ready=$(gh api repos/{owner}/{repo}/issues/<n>/timeline \
          --jq '.[]|select(.event=="ready_for_review")|.created_at' | head -1)
gh pr view <n> --json commits | jq --arg r "$ready" \
  '{after: ([.commits[]|select(.authoredDate > $r)]|length), total: (.commits|length)}'
```

Two things that bite here, both verified: `gh`'s own `--jq` takes no `--arg`, so pipe to real `jq` when the filter needs a variable. And `|` rebinds the input for everything after it, so a second reference to `.commits` must be parenthesized or it evaluates against the piped array instead of the original object.

Declared against actual, from the PR body and the two scripts:

```bash
PR_BODY="$(gh pr view <n> --json body -q .body)" uv run scripts/concept_budget.py
uv run scripts/quality_delta.py
```

Read this as a ceiling check, never as an accuracy score. Hitting your own declared number proves nothing: a PR declaring +1000 and shipping +1000 is unexamined, not precise.

Then read the first review of each PR and record the defect it found, if any. Group the defects by shape. In v5.0.0 all four shared one: the agent tested what it built and not what it changed.

## 3. Agents: reviewer

Verdict rounds, and which of them were productive:

```bash
gh api repos/{owner}/{repo}/pulls/<n>/reviews --jq '.[]|select(.body|length>200)|"\(.submitted_at) \(.body[0:80])"'
```

A round is a **loop** if it re-raised a finding already raised, or reviewed a tree unchanged since the last round. It is **productive** if it raised something new about code that changed. Keep them separate: a round that found a defect introduced by the previous round's fix is the system working, and one metric would punish it identically to a wasted re-stamp.

Comment routing, inline against verdict body:

```bash
gh api repos/{owner}/{repo}/pulls/<n>/comments --jq 'length'
gh api repos/{owner}/{repo}/issues/<n>/comments --jq 'length'
```

Review prose against the PR body it judged:

```bash
gh pr view <n> --json body -q .body | perl -0pe 's/```.*?```/ /gs' | wc -w
gh api repos/{owner}/{repo}/pulls/<n>/reviews --jq '.[]|select(.body|length>200)|.body' | wc -w
```

Escape rate: findings filed against a PR after it merged. Match the `finding:` issues from phase 2 to the PR that introduced the code, then divide by PRs reviewed.

## 4. PR shape

```bash
gh pr view <n> --json commits --jq '.commits[]|.messageHeadline'
```

Look for duplicate headlines (an amend artifact), merge commits (`update-branch` without `--rebase`), and commits scoped by the review round rather than by what changed. Squash-merge keeps all of this out of `dev`, so it costs review attention rather than permanent record; weight it accordingly.

Body size against the cap, with the same command the caps are defined in terms of:

```bash
gh pr view <n> --json body -q .body | perl -0pe 's/```.*?```/ /gs' | wc -w
```
