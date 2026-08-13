# Contributing to agentdeck

## New here?

AgentDeck SDK is a production runtime around agents you already have — it supplies sessions,
streaming, an event log, human approval and run control, and leaves execution to the OpenAI
Agents SDK and LangGraph.

Before picking up an issue, run one of the projects in [`examples/`](examples/) as a user would
(`pip install agentdeck-sdk`, then `python run.py`). Fifteen minutes there makes the rest of this
file, and most issues, read very differently.

Then take a
[`good first issue`](https://github.com/agentdecksdk/agentdeck/labels/good%20first%20issue) or
[`help wanted`](https://github.com/agentdecksdk/agentdeck/labels/help%20wanted). Comment on it to
claim it — nobody else will be assigned while you are working, and a stalled PR is not taken over
without asking you first.

## Branch model

- **`dev`** — default branch. All PRs target `dev`.
- **`main`** — release branch. Only fast-forwarded from `dev` when cutting a release.
- PRs are **squash-merged**, so a merged branch's tip never becomes an ancestor of
  `dev`. `git branch --merged` therefore reports *every* branch as unmerged and is
  useless here — prune by PR state instead:
  `gh pr list --head "$branch" --state all --json state -q '.[0].state'`.

## Releasing (x.y.z)

1. Bump `version` in `pyproject.toml` on `dev` and move the **Unreleased**
   CHANGELOG entries under the new version heading.
2. Merge `dev` → `main`.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The release workflow verifies the tag matches `pyproject.toml`, runs the full
   gate, builds sdist + wheel, and publishes a GitHub Release.

## Setup

```bash
git clone https://github.com/agentdecksdk/agentdeck.git && cd agentdeck
uv venv && uv pip install -e ".[dev,serve]"
pre-commit install          # ruff + ty + hygiene hooks on every commit
```

Working in a git worktree (or any second checkout): `uv` reads an inherited
`VIRTUAL_ENV`, so run `unset VIRTUAL_ENV` first, or pass
`--python <worktree>/.venv/bin/python`. This applies to **every** `uv pip`
subcommand, `uninstall` included — otherwise an install lands in the other
checkout's venv and repoints its editable `agentdeck` at your tree, and an
uninstall strips packages the other checkout still needs.

## Before you push

```bash
make check                  # lint + typecheck + lint-imports + tests — CI runs exactly this
```

## Docs are part of the change, not a follow-up

- **`docs-site/` is the canonical user-facing contract** and `docs/` is never
  published. A PR that changes user-visible behavior (HTTP/SSE surface, CLI,
  events, run control) updates the affected `docs-site/` pages in the same PR —
  anti-rot tests parse the site's code samples, so a stale sample fails CI.
- **When implementation diverges from a design doc**, the same PR amends the doc
  with a dated note. Never code around a doc silently.
- **Every user-visible change gets a CHANGELOG entry** under **Unreleased**.
  Entries are release notes, not dev narrative: written for a user of the
  package, in Keep-a-Changelog category order (`Added / Changed / Deprecated /
  Removed / Fixed / Security`), and never citing internal design docs,
  milestones, or issue plumbing — they must read cleanly when attached to a
  GitHub Release as-is.

## Ground rules

The full standards live in **`docs/coding-standards.md`** — read it before any
non-trivial change; this list is the two-minute version.

- **agentdeck owns configuration, not execution.** Execution stays in the
  OpenAI Agents SDK / LangGraph. If your change runs things, it belongs in a
  bundle or an app, not here.
- **`Deck` is the one composition root.** `Deck(agents=…, workflows=…)` and
  `Deck.from_project()`, which discovers the same arguments from `./.agentdeck/`
  — two front doors, one catalog. Don't add a third, and don't add alternative
  catalog mechanisms behind the ones that exist.
- **One `Deck` per process.** Constructing a second while one is live raises.
- **Typed boundaries**: new public functions carry annotations. `ty` must pass,
  but never by contorting the code — at deliberate SDK shims, or where appeasing
  the checker would make the code smellier, use a narrow `# ty: ignore[rule]`
  with a one-line reason.
- **Comments are short, focused, and rare.** Only where the code is genuinely
  hard to follow — a non-obvious path, a decision, a key invariant. A comment
  stands alone in its own words: never point at a doc section, paragraph, or
  bullet.
- **Every non-trivial change lands with a test.** No frameworks beyond pytest.
- **New dependencies need a reason** the stdlib or an existing dep can't cover.
- **`core/` imports stdlib + pydantic only** — no exceptions; import-linter
  enforces it. New event kinds and envelope changes land only in dedicated
  schema PRs.
- **Goldens never auto-update.** `tests/golden/` and `tests/core/snapshots/`
  change only via `make golden`, deliberately, with the reason in the PR.
- Optional integrations (Langfuse, MCP) must degrade gracefully when
  unconfigured — never crash an unconfigured run.

## What to expect from us

- A first maintainer response on your PR within a day or so, and a review that
  says what needs to change rather than changing it for you.
- Questions are welcome in
  [Discussions](https://github.com/agentdecksdk/agentdeck/discussions) — an
  issue that turns out to be a question gets moved there, not closed.

If AgentDeck SDK was useful, or you enjoyed contributing, starring the
repository helps other developers discover the project.
