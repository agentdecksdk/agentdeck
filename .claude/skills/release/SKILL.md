---
name: release
description: Cut an agentdeck release  -  bump version, update CHANGELOG, merge dev to main, and tag vX.Y.Z.
---

# Release agentdeck

Follow the release process strictly. All work starts on `dev`; `main` is release-only,
and `dev` is the repository's default branch.

## Two PRs, two issues

A release needs two labeled `chore` issues and two PRs, not one of each.

| PR | base | issue | closing keyword |
|---|---|---|---|
| bump | `dev` | its own labeled `chore` issue | `Closes #N`, works: `dev` is default |
| promotion | `main` | its own labeled `chore` issue | `Refs #N` only, close by hand: `main` is not default, so GitHub never creates the closing reference |

`git push origin dev` is rejected directly ("push declined due to repository rule violations").
The bump goes through its own PR into `dev`.

## Release Steps

1. **Version:** Confirm SemVer bump (`pyproject.toml` and `CHANGELOG.md`).
2. **Preflight:** Verify clean working tree, non-empty `[Unreleased]` section, and `make check`
   is 100% green. Run it unpiped (`make check`, not `make check | tail`) so its own exit code is
   what you read; a pipe reports the last command's status, not the gate's.
3. **Bump PR into `dev`:**
   - Update `version` in `pyproject.toml`.
   - Move Unreleased entries to `## [X.Y.Z] - YYYY-MM-DD` in `CHANGELOG.md` with compare links.
   - Run `python scripts/generate_docs_reference.py` once, then verify
     `python scripts/generate_docs_reference.py --check`.
   - Commit: `chore(release): vX.Y.Z`. Open the PR against the labeled bump issue
     (`Closes #N`), wait for every required check, merge.
4. **Promotion PR, `dev` to `main`:** Open it against the labeled promotion issue (`Refs #N`).
   `Issue hygiene`, `slop`, and `Check affected documentation` skip themselves on a `main`-based
   PR: each already ran the real per-PR version of itself against `dev` at merge time, and this
   diff is the whole release cycle re-evaluated, not a new change. Wait for `check` and
   `Build documentation`, then merge and close the promotion issue by hand.
5. **Tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`. `release.yml` publishes to PyPI on this
   push; there is no undo once the tag lands.
6. **Deploy:** In the `prod/` worktree (detached on a tag on purpose, so the public only sees
   something released), run `./deploy-prod.sh vX.Y.Z`. It rebuilds `docs-site`, restarts
   `agentdeck-docs-static.service`, and health-checks `127.0.0.1:4321`.
7. **Report:** Return the GitHub release URL.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
