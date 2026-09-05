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

A PR body closing more than one issue needs one `Fixes #N` (or `Closes #N`) per line: GitHub's
closing-keyword parser only recognizes the first reference in a comma-separated list, silently
leaving the rest open (#541).

## Release Steps

1. **Version:** Confirm SemVer bump (`pyproject.toml` and `CHANGELOG.md`).
2. **Preflight:** Verify clean working tree, non-empty `[Unreleased]` section, and `make check`
   is 100% green. Run it unpiped (`make check`, not `make check | tail`) so its own exit code is
   what you read; a pipe reports the last command's status, not the gate's.
3. **Bump issue:** `python scripts/release_bump.py issues X.Y.Z` creates (or reuses) the `vX.Y.Z`
   milestone, assigns it to every issue `Fixes`/`Closes`-referenced since the last tag, opens the
   labeled `chore: cut and ship vX.Y.Z` issue, and prints its number.
4. **Bump PR into `dev`:**
   - `python scripts/release_bump.py bump X.Y.Z` updates `pyproject.toml`'s version and moves
     `CHANGELOG.md`'s `[Unreleased]` entries to a dated `## [X.Y.Z]` heading with compare links.
   - Run `python scripts/generate_docs_reference.py` once, then verify
     `python scripts/generate_docs_reference.py --check`.
   - Snapshot the docs site only if this release removes or changes a documented surface, not on
     every release (`docs/delivery/docs-site-plan.md` DS-D3). A patch documenting the same
     surface owes nothing.
   - Commit: `chore(release): vX.Y.Z`. Open the PR against the bump issue from step 3
     (`Closes #N`), wait for every required check, merge.
5. **Promotion issue:** `python scripts/release_bump.py promote X.Y.Z` opens the labeled
   `chore: release vX.Y.Z to main` issue in the same milestone and prints its number.
6. **Promotion PR, `dev` to `main`:** Open it against the promotion issue from step 5 (`Refs #N`).
   `Issue hygiene`, `slop`, and `Check affected documentation` skip themselves on a `main`-based
   PR: each already ran the real per-PR version of itself against `dev` at merge time, and this
   diff is the whole release cycle re-evaluated, not a new change. Wait for `check` and
   `Build documentation`, then merge and close the promotion issue by hand.
7. **Tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`. `release.yml` publishes to PyPI on this
   push; there is no undo once the tag lands.
8. **Close the milestone:** `python scripts/release_bump.py close-milestone X.Y.Z`, right after
   the tag push. Every past release skipped this: `v5.0.0` through `v5.1.0` all shipped with 0
   open issues but stayed open on GitHub.
9. **Deploy:** In the `prod/` worktree (detached on a tag on purpose, so the public only sees
   something released), run `./deploy-prod.sh vX.Y.Z`. It rebuilds `docs-site`, restarts
   `agentdeck-docs-static.service`, and health-checks `127.0.0.1:4321`.
10. **Report:** Return the GitHub release URL.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
