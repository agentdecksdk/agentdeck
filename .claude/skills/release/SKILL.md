---
name: release
description: Cut an agentdeck release  -  bump version, update CHANGELOG, merge dev to main, and tag vX.Y.Z.
---

# Release agentdeck

Follow the release process strictly. All work starts on `dev`; `main` is release-only.

## Release Steps
1. **Version:** Confirm SemVer bump (`pyproject.toml` and `CHANGELOG.md`).
2. **Preflight:** Verify clean working tree, non-empty `[Unreleased]` section, and `make check` is 100% green.
3. **Bump on `dev`:**
   - Update `version` in `pyproject.toml`.
   - Move Unreleased entries to `## [X.Y.Z] - YYYY-MM-DD` in `CHANGELOG.md` with compare links.
   - Run `python scripts/generate_docs_reference.py` twice.
   - Commit: `chore(release): vX.Y.Z`.
4. **Push `dev`:** Wait for CI green.
5. **Merge to `main`:** Merge `dev` into `main` and push.
6. **Tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`.
7. **Redeploy Docs:** If release worktree exists (`~/prjs/agentdeck-docs-release`), run `./redeploy.sh vX.Y.Z` and verify served output.
8. **Report:** Return the GitHub release URL.

## Ground Rules
- Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
