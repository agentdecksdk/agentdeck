# How issues, findings and PRs move through this project

Everything lives as a GitHub issue with a milestone, a label set and a `Status` on the
[AgentDeck Project](https://github.com/users/sagi5060/projects/5). `docs/delivery/roadmap-*.md` and
`findings-register.md` hold the *reasoning*; GitHub holds the *state*, and wins if they disagree  -
`make roadmap-sync` regenerates the tables that say so.

## Filing a finding

A finding is something observed and not yet triaged  -  from a review, an audit, or a reproduced
surprise. Never a bare area prefix; that's for planned work.

```
gh issue create -t "finding: <what>" --label finding
```

Body follows the house structure below; #177, #178, #223, #226 are the tone to match. A finding
stays a `finding` until it gets a disposition  -  solved, rejected, or scheduled against a
milestone  -  recorded in `findings-register.md` if the investigation is worth a paragraph, closed
with a comment if it isn't.

## Opening an issue

Use the `open-issue` skill (`/open-issue` or ask for it directly)  -  it files in the repo's house
style: **Problem / Proposed shape / Notes / Done when**. Two things differ by issue type:

- **Bug.** Problem section carries the exact traceback and a minimal repro command. Proposed shape
  is often short  -  "fix it so the repro passes." Label `bug`, plus the `area:*` it touches.
- **Feature.** Proposed shape carries the real weight  -  concrete API/behavior, code snippets of the
  intended usage, what stays unchanged, what's deliberately out of scope. Label `feature` or
  `enhancement`, plus `area:*`. If the shape isn't settled yet, label `needs-ruling` instead of
  guessing  -  a design question blocks implementation, not the reverse (see `roadmap-v3.1.md` §3,
  ruling 2).

Planned work (not derived from a finding) uses a bare area prefix in the title  -  `deck:`,
`docs-site:`, `control:`  -  never `finding:`.

Set a milestone at filing time if you know which release it belongs to; leave it unmilestoned if
it's a question before it's a task (that's a real, named state  -  see `roadmap-v3.1.md`'s
"Unmilestoned" section, not an oversight).

## Starting work on an issue

Default to the `ship-issue` skill (`/ship-issue N`): `deck-dev` implements on an isolated worktree
and opens a **draft PR on its first commit**, `deck-reviewer` gates it against the issue's "Done
when" list, findings get fixed, then it merges to `dev`. A trivial nit  -  a comment, a missing
timeout  -  can be fixed inline instead.

`needs-ruling` issues aren't picked up by `ship-issue`  -  they need a decision first, not a worktree.

## How this shows up on the Project board

`Status` is a pipeline stage, not a priority. It changes at these points, in order:

| Stage | Status | Set by |
|---|---|---|
| Filed, blocked on a design decision | **Needs ruling** | whoever files it, or whoever notices the design question |
| Filed, ready to pick up | **Backlog** | default state for a new issue |
| `deck-dev` opens its draft PR | **In progress** | `ship-issue` / whoever starts the work |
| PR marked ready, `deck-reviewer` running | **In review** | `ship-issue` on `gh pr ready` |
| PR merged | **Done** | automatic  -  GitHub closes the issue and the item settles here |

`blocked`, `finding`, `finding:triaged` and the `area:*`/`bug`/`feature` labels are orthogonal to
`Status`  -  they describe *what* the issue is, `Status` describes *where it is right now*.

### Start date / Target date

Set from real events, never guessed:

- **Start date** = the day work actually started  -  the day its PR opened (`gh pr view <N> --json
  createdAt`), not the day it was picked up in conversation. Set when `Status` moves to
  **In progress**.
- **Target date** = the day its PR merged. Set when `Status` moves to **Done**.

No PR means no real start date: leave the field empty and `Status` at **Backlog** rather than flip
it on intent. *(Correction made 2026-08-12: #221 was flagged "In progress" with no open PR and no
evidence of active work, and was moved back to **Backlog**.)*

### The Project's views

Four, all on the [Project](https://github.com/users/sagi5060/projects/5):

- **Backlog**, **Board**  -  the stock views. Board is filtered to the *current* milestone
  (`milestone:"v3.1  -  hardening"` as of 2026-08-12)  -  update that filter by hand
  (`updateProjectV2View` over the GraphQL API, or the view's own filter box) each time the active
  milestone rolls over, or it silently shows a finished release.
- **Roadmap**  -  Roadmap layout (GitHub's native timeline view), grouped by milestone. Reads
  `Milestone`'s own due date, so it moves automatically once due dates are set  -  see below.
- **Findings**  -  Table layout, filtered to `label:finding`. Replaces scanning
  `findings-register.md` for what's currently open.

The stock **Current iteration** view was deleted: this project sequences by milestone, not
GitHub's separate Iteration field.

### Milestone due dates

Each release milestone (`v3.1`, `v3.2`, `v3.3`, `v3.4`) carries a `due_on` date, one week apart,
starting from v3.1's target. `docs-site` is excluded  -  it runs parallel to the release train and
never gates one (`roadmap-v3.1.md` §4). Move them with:

```
gh api -X PATCH repos/agentdecksdk/agentdeck/milestones/<number> -f due_on="<date>T00:00:00Z"
```
