---
name: release
description: Cut an agentdeck release — bump the version, move CHANGELOG Unreleased entries, merge dev to main, tag vX.Y.Z. Use when the user says "release", "cut a release", "bump version", or "ship vX.Y.Z".
---

# Release agentdeck

Follow CLAUDE.md's release process exactly. All work happens on `dev` first; `main` is release-only.

1. **Pick the version.** SemVer against the CHANGELOG's Unreleased section: breaking → major (or minor pre-1.0), features → minor, fixes only → patch. Confirm the number with the user if it isn't obvious or they didn't specify.
2. **Preflight on `dev`:** working tree clean, `git pull`, CHANGELOG has a non-empty Unreleased section, `make check` green.
   Also `grep -rn '@v[0-9]' docs-site/content/` and update any version the install instructions pin. Nothing gates this — `test_docs_site.py` only parses `python` fences and `docs-check.yml` only checks the build produced a page — so a stale pin ships silently, and the getting-started page once told users to install v1.2.1 on a 2.0.0b4 package.
3. **Bump on `dev`:** set `version` in `pyproject.toml`; in `CHANGELOG.md` move the Unreleased entries under a new `## [X.Y.Z] - <today>` heading (leave an empty Unreleased). One commit: `chore: release vX.Y.Z`.
4. **Push `dev`, wait for CI green.**
5. **Merge to `main`:** `git checkout main && git pull && git merge dev` (or a dev→main PR if branch protection requires it), push.
6. **Tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`. release.yml verifies the tag matches `pyproject.toml`, runs the gate, and publishes the GitHub Release — watch the run and report its result.
7. Back to `dev`. Report the release URL.

If any step fails, stop and report — never re-tag or force-push a tag.
