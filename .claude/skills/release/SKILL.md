---
name: release
description: Cut an agentdeck release — bump the version, move CHANGELOG Unreleased entries, merge dev to main, tag vX.Y.Z. Use when the user says "release", "cut a release", "bump version", or "ship vX.Y.Z".
---

# Release agentdeck

Follow CLAUDE.md's release process exactly. All work happens on `dev` first; `main` is release-only.

1. **Pick the version.** SemVer against the CHANGELOG's Unreleased section: breaking → major (or minor pre-1.0), features → minor, fixes only → patch. Confirm the number with the user if it isn't obvious or they didn't specify.
2. **Preflight on `dev`:** working tree clean, `git pull`, CHANGELOG has a non-empty Unreleased section, `make check` green.
   Also `grep -rn '@v[0-9]' docs-site/content/` and update any version the install instructions pin. Nothing gates this — `test_docs_site.py` only parses `python` fences and `docs-check.yml` only checks the build produced a page — so a stale pin ships silently, and the getting-started page once told users to install v1.2.1 on a 2.0.0b4 package.
3. **Bump on `dev`:** set `version` in `pyproject.toml`; in `CHANGELOG.md` move the Unreleased entries under a new `## [X.Y.Z] - <today>` heading, and add the `[X.Y.Z]:` compare link beside the `[Unreleased]:` one at the bottom.
   Then run `scripts/generate_docs_reference.py` **twice** — `llms-full.txt` renders against the pre-regeneration `changelog.mdx`, so one pass always leaves `test_generated_reference` red. Expect `changelog.mdx` to lose most of the previous release's body: the generator inlines only the current release and drops earlier ones to a link table. That is correct, not a bad diff.
   One commit: `chore(release): vX.Y.Z`.
4. **Push `dev`, wait for CI green.**
5. **Merge to `main`:** `git checkout main && git pull && git merge dev` (or a dev→main PR if branch protection requires it), push.
6. **Tag:** `git tag vX.Y.Z && git push origin vX.Y.Z`. release.yml verifies the tag matches `pyproject.toml`, runs the gate, publishes the GitHub Release, and dispatches `docs-pages.yml` — watch the run and report its result.
7. **Redeploy the canonical docs site. This is not automated and is the step most likely to be forgotten.** `docs-pages.yml` only updates the GitHub Pages *mirror*; `agentdecksdk.com` is served from a separate worktree pinned to a tag, so until you do this the published docs still describe the previous release:

   ```bash
   cd ~/prjs/agentdeck-docs-release && ./redeploy.sh vX.Y.Z
   ```

   `redeploy.sh` is untracked on purpose, so it survives the tag checkout. Verify by what is **served**, never by the build log:

   ```bash
   curl -sS http://127.0.0.1:4321/known-issues/ | grep -oE '<title>[^<]*'   # must not be the home page
   grep -rhoE 'https?://[a-z0-9.:/-]*(agentdecksdk|localhost)[a-z0-9.:/-]*' \
     ~/prjs/agentdeck-docs-release/docs-site/out/_next/static/chunks/*.js | sort -u
   ```

   The second must print `https://agentdecksdk.com` and nothing else. A `localhost` hit means a build-time variable was unset, which ships its fallback to every visitor.
8. Back to `dev`. Report the release URL.

If any step fails, stop and report — never re-tag or force-push a tag.
