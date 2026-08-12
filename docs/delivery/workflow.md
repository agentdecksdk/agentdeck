# How issues, findings and PRs move through this project

The short version: everything lives as a GitHub issue with a milestone, a label set and a
`Status` on the [AgentDeck Project](https://github.com/users/sagi5060/projects/5). The
`docs/delivery/roadmap-*.md` and `findings-register.md` files hold the *reasoning* (why a wave is
sequenced the way it is, what an investigation found); GitHub holds the *state*. If they disagree,
GitHub is right — `make roadmap-sync` regenerates the tables that say so.

## Filing a finding

A finding is "something we observed and need to look at" — not yet a bug, not yet planned work.
It comes from a review, an audit, or a reproduced surprise, and it is never filed under a bare area
prefix (that's for planned work — see below).

```
gh issue create -t "finding: <what>" --label finding
```

Body follows the same house structure as any issue (below). Existing examples to match for tone:
#177, #178, #223, #226. A finding stays a `finding` until someone gives it a disposition — solved,
rejected, or scheduled against a milestone — recorded in `findings-register.md` if it's the kind of
thing worth a paragraph of investigation, or just closed with a comment if it isn't.

## Opening an issue

Use the `open-issue` skill (`/open-issue` or ask for it directly) — it files in the repo's house
style: **Problem / Proposed shape / Notes / Done when**. Two things differ by issue type:

- **Bug.** Problem section carries the exact traceback and a minimal repro command. Proposed shape
  is often short — "fix it so the repro passes." Label `bug`, plus the `area:*` it touches.
- **Feature.** Proposed shape carries the real weight — concrete API/behavior, code snippets of the
  intended usage, what stays unchanged, what's deliberately out of scope. Label `feature` or
  `enhancement`, plus `area:*`. If the shape isn't settled yet, label `needs-ruling` instead of
  guessing — a design question blocks implementation, not the reverse (see `roadmap-v3.1.md` §3,
  ruling 2).

Planned work (not derived from a finding) uses a bare area prefix in the title — `deck:`,
`docs-site:`, `control:` — never `finding:`.

Set a milestone at filing time if you know which release it belongs to; leave it unmilestoned if
it's a question before it's a task (that's a real, named state — see `roadmap-v3.1.md`'s
"Unmilestoned" section, not an oversight).

## Starting work on an issue

Default to the `ship-issue` skill (`/ship-issue N`) rather than working inline — it orchestrates
the whole pipeline: `deck-dev` implements on an isolated worktree and opens a **draft PR on its
first commit**, `deck-reviewer` gates it against the issue's "Done when" list, findings get fixed,
then it merges to `dev`. A trivial nit (a comment, a missing timeout) can be fixed inline instead
of spinning up the pipeline.

`needs-ruling` issues aren't picked up by `ship-issue` — they need a decision first, not a worktree.

## How this shows up on the Project board

`Status` is a pipeline stage, not a priority. It changes at these points, in order:

| Stage | Status | Set by |
|---|---|---|
| Filed, blocked on a design decision | **Needs ruling** | whoever files it, or whoever notices the design question |
| Filed, ready to pick up | **Backlog** | default state for a new issue |
| `deck-dev` opens its draft PR | **In progress** | `ship-issue` / whoever starts the work |
| PR marked ready, `deck-reviewer` running | **In review** | `ship-issue` on `gh pr ready` |
| PR merged | **Done** | automatic — GitHub closes the issue and the item settles here |

`blocked`, `finding`, `finding:triaged` and the `area:*`/`bug`/`feature` labels are orthogonal to
`Status` — they describe *what* the issue is, `Status` describes *where it is right now*.

### The two saved views (set up once, by hand)

`gh`/the GitHub API can't create saved Project views — this is a one-time manual step in the web
UI:

1. Open the [Project](https://github.com/users/sagi5060/projects/5), click **+ New view**.
2. **Roadmap** — Table layout. Toolbar: **Group by** → Milestone, **Sort by** → Priority.
   Replaces the wave tables in `roadmap-v3.md`/`roadmap-v3.1.md` as the live view.
3. **+ New view** again — **Findings** — Table layout. Toolbar: **Filter** → `label:finding`.
   Replaces scanning `findings-register.md` for what's currently open.

Both views read live off the same `Status`/`Milestone`/`Priority` fields every issue already
carries — no extra bookkeeping once they exist.
