# How AgentDeck Is Developed and Shipped

The engineering *process* here is two projects wearing one coat. The automation is exceptional: a CI gate whose every step cites the incident that motivated it, live Postgres and Redis contract tests, a skip-hunter that refuses to let the suite go green having measured nothing, and a release path with trusted PyPI publishing and a tag/version guard that has already caught a real mistake. The enforcement around that automation is close to nonexistent: no branch protection on any branch, 145 of 164 merged PRs with no recorded review, a PR merged 2m41s before its own CI finished, and a production-facing assistant that runs as a `uvicorn` process on the maintainer's laptop.

## Findings

### CI steps are annotated with the incident each one prevents [GOOD] (severity: high)
Every non-obvious step in `ci.yml` carries a comment naming the issue that made it necessary. This is the single best thing about the process: the gate documents its own scar tissue, so nobody deletes a step because it "looks redundant".
```
      # durability pulls in the sqlite checkpointer that the langgraph half of the
      # contract suite and UC2's kill/restart/approve tests need  -  omitting it here
      # let ~16 tests skip silently in the gate of record for months (#33).
      # observability was the same mistake one extra over (#142): without it the 12
      # tests in test_observability.py and test_langfuse_tracer.py skip, which is how
      # #141's keyless-AsyncOpenAI regression shipped green.
```
Evidence: `.github/workflows/ci.yml:70`

### Stores are contract-tested against real servers, not mocks [GOOD] (severity: high)
Both `ci.yml` and `release.yml` stand up Redis 7 and Postgres 16 service containers with health checks and hand the DSNs to the suite. A store proved only against a double is not proved, and this repo acts on that.
```yaml
    services:
      redis:
        image: redis:7-alpine
      postgres:
        image: postgres:16-alpine
    env:
      AGENTDECK_TEST_REDIS_URL: redis://127.0.0.1:6379/15
      AGENTDECK_TEST_POSTGRES_DSN: postgresql://postgres:postgres@127.0.0.1:5432/agentdeck_test
```
Evidence: `.github/workflows/ci.yml:18`, `.github/workflows/release.yml:26`

### The gate hunts its own skips [GOOD] (severity: high)
Two steps exist purely to stop a green run that measured nothing: one imports every extra the suite guards on, and one greps the JUnit XML for skip reasons that mean a subsystem dropped out. Matching reasons rather than counting skips is the right call, because the count drifts whenever the contract matrix grows.
```
      - name: No subsystem dropped out of the gate
        run: |
          if grep -qE 'needs the \[[a-z]+\] extra|no Postgres at|no Redis at' /tmp/pytest-results.xml; then
            echo "::error::a subsystem skipped out of the gate  -  an extra or a service container is missing"
            exit 1
          fi
```
Evidence: `.github/workflows/ci.yml:82` and `.github/workflows/ci.yml:98`

### The release gate is the merge gate, not a narrower one [GOOD] (severity: high)
`release.yml` re-runs the identical install, extras-importable check, lint, typecheck, import-linter, pytest, and skip-hunt, with the same service containers. The common shortcut here is a tag job that only builds and uploads; this repo refused it and said why in the file.
```yaml
      - name: Install and check
        # Same extras as ci.yml  -  a release build must run the same gate as a merge, not a
        # narrower one.
```
Evidence: `.github/workflows/release.yml:64`

### PyPI publishing uses trusted publishing with `id-token` scoped to one job [GOOD] (severity: high)
No long-lived API token exists in the repo. `id-token: write` is granted to the `pypi` job alone rather than at workflow level, and the job runs in a named environment that forms half of the Trusted Publisher identity. Verified live: the `pypi` job succeeded on the v4.0.5 release run.
```yaml
    permissions:
      id-token: write
    environment:
      name: pypi
```
Evidence: `.github/workflows/release.yml:137`; `gh run view 32243696181 --json jobs` -> `{"conclusion":"success","name":"pypi"}`
Ref: https://docs.pypi.org/trusted-publishers/

### `pull_request_target` is treated as the hazard it actually is [GOOD] (severity: high)
The first-contribution workflow needs a writable token on fork PRs, so it uses `pull_request_target` and then locks down everything that makes that trigger dangerous: top-level `permissions: {}`, no checkout of the head, and the one PR-controlled value passed through `env:` rather than spliced into the shell. Most repos get this wrong.
```yaml
permissions: {}
...
    env:
      AUTHOR: ${{ github.event.pull_request.user.login }}
```
Evidence: `.github/workflows/first-contribution.yml:16`, and the reasoning at `:3`
Ref: https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/

### The tag/version guard caught a real release mistake [GOOD] (severity: medium)
`release.yml` compares the tag to `pyproject.toml` before doing anything. On v4.0.5 it fired: the first run failed at that step, and only a re-dispatch after the bump landed succeeded. A guard that has never fired is a guess; this one has evidence.
```bash
          if [ "$version" != "$tag" ]; then
            echo "pyproject version $version != tag $tag" >&2
            exit 1
          fi
```
Evidence: `.github/workflows/release.yml:60`; `gh run view 32242843752` -> failed step `Verify tag matches pyproject version`

### Release notes must be curated, with no generated fallback [GOOD] (severity: medium)
The workflow extracts the CHANGELOG section for the tag and hard-fails when it is missing. There is deliberately no `--generate-notes` escape, because a commit-list release note is the bug the step exists to prevent. The known truncation limit is even written down.
```bash
          if [ ! -s /tmp/release-notes.md ]; then
            echo "No CHANGELOG section found for heading '## [$version]'  -  add release notes to CHANGELOG.md before tagging." >&2
            exit 1
          fi
```
Evidence: `.github/workflows/release.yml:103`

### Every one of 21 tags has a dated, curated CHANGELOG section [GOOD] (severity: medium)
Keep-a-Changelog format, category ordering, dates on all 21 versions including the four betas. For a project 25 days old that has shipped four majors, this is unusually disciplined bookkeeping.
```
## [Unreleased]
## [4.0.5] - 2026-08-19
## [4.0.4] - 2026-08-19
...
## [0.1.0] - 2026-07-26
```
Evidence: `CHANGELOG.md:9` onward; `grep -c '^## ' CHANGELOG.md` -> 22 (21 versions plus Unreleased), against 21 tags
Ref: https://keepachangelog.com/

### CI is fast enough that nobody is tempted to skip it [GOOD] (severity: medium)
Median CI wall clock is 2 minutes across 146 runs, with 138 successes and 7 failures. Fast gates get respected; 20-minute gates get bypassed.
```
{"workflow":"CI","runs":146,"success":138,"failure":7,"reruns":1,"median":2}
```
Evidence: `gh run list --repo agentdecksdk/agentdeck --limit 300 --json name,conclusion,attempt,createdAt,updatedAt`

### PR descriptions are real documents, not one-liners [GOOD] (severity: medium)
Median merged-PR body is 5,499 characters and not one of the 164 merged PRs has an effectively empty body. The template asks for What / Why plus a nine-item checklist covering CHANGELOG, docs, goldens, and design-doc divergence, and the bodies show it is answered.
```
{"total":164,"empty_body":0,"median_body_len":5499}
```
Evidence: `gh pr list --state merged --limit 200 --json body` aggregated; template at `.github/PULL_REQUEST_TEMPLATE.md:1`

### Draft-PR-first is a real practice, and the review bot is configured around it [GOOD] (severity: medium)
Four of five sampled recent PRs carry a `ready_for_review` timeline event, so they opened as drafts and flipped when the author's gate went green. `.coderabbit.yaml` then aligns the bot to that exact moment instead of reviewing work its own author has not gated.
```yaml
  auto_review:
    enabled: true
    drafts: false
    auto_incremental_review: false
```
Evidence: `.coderabbit.yaml:31`; `gh api repos/.../issues/{358,354,349,338}/timeline` -> `ready_for_review`

### The review-bot config encodes the repo's actual invariants, not generic advice [GOOD] (severity: medium)
`path_instructions` tell the reviewer that `core/` may import stdlib and pydantic only, that events are never hand-built dicts, that non-default backends resolve lazily, that the `openai==2.32.0` pin is deliberate, and that install lines say `agentdeck-sdk`. Generated and frozen files are excluded, with a reason per exclusion.
```yaml
    - path: "agentdeck/core/**"
      instructions: >-
        `core/` may import stdlib and pydantic ONLY ... This is enforced by import-linter
        (`.importlinter`, `make lint-imports`), so flag any new import here as a
        build-breaking change, not a style preference.
```
Evidence: `.coderabbit.yaml:56`

### Implementer and reviewer are separate agents with a review-only mandate [GOOD] (severity: medium)
The agent pipeline does not let one agent write and bless its own work. `deck-dev` implements in an isolated worktree and opens a draft PR; `deck-reviewer` is explicitly forbidden from touching the branch and must run `make check` itself rather than trust the PR body.
```
You review one agentdeck PR as the merge gate. REVIEW ONLY  -  never push commits or modify the PR.
```
Evidence: `.claude/agents/deck-reviewer.md:8`; `.claude/agents/deck-dev.md:26`

### Agent configuration has one source of truth and a generator, not three hand-maintained copies [GOOD] (severity: medium)
`.codex/agents/*.toml` and `.agents/skills/**` are both produced from `.claude/` by a script, with a do-not-edit header. The skill files are byte-identical across the two trees. Multi-harness agent configs are exactly where silent drift lives, and this is the right answer to it.
```
# Generated by scripts/sync_claude_to_codex.py; do not edit.
```
Evidence: `.codex/agents/deck-reviewer.toml:1`; `scripts/sync_claude_to_codex.py:13`; `diff .claude/skills/release/SKILL.md .agents/skills/release/SKILL.md` -> identical

### Commit messages explain why, at a quality most human teams do not reach [GOOD] (severity: medium)
Conventional-commit subjects with bodies that name the mechanism and the symptom. Across 30 sampled dev commits, the non-dependabot bodies read like short incident notes rather than restatements of the diff.
```
fix(ci): first-contribution jobs need pull-requests: write to comment (#360)

gh pr comment posts via the addComment GraphQL mutation, which needs
pull-requests: write. Both jobs only granted pull-requests: read, so
every welcome/thanks comment failed with "Resource not accessible by
integration" (surfaced on PR #359).
```
Evidence: `git log origin/dev -30 --pretty='%h %s%n%b'`, commit `f1f650c`

### CHANGELOG conflicts are solved by git's own merge driver [GOOD] (severity: low)
Every PR appends to `[Unreleased]`, so concurrent PRs collide on a file they do not disagree about. The fix is one `.gitattributes` line and a written-down trade, plus the upgrade path if concurrency ever justifies it. No dependency, no tooling.
```
CHANGELOG.md merge=union
```
Evidence: `.gitattributes:18`

### The binary-file rule runs in CI, not only in a pre-commit hook [GOOD] (severity: medium)
The reasoning is the useful part: a pre-commit hook never runs for a contributor who has not installed hooks, which is most first PRs, and a size threshold that clears a 336KB `uv.lock` also clears every image. So the rule is location, checked with a diff against the empty tree.
```bash
          strays=$(git diff --numstat 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD \
                     | awk '$1 == "-" { print $3 }' | grep -v '^\.github/assets/' || true)
```
Evidence: `.github/workflows/ci.yml:58`

### Docs deploy from the released tag, not from the branch tip [GOOD] (severity: medium)
`docs-pages.yml` checks out `github.event.release.tag_name`, so the published site describes the released package rather than unreleased `dev`. The chaining comment also correctly identifies GitHub's recursion guard on `GITHUB_TOKEN`-created releases.
```yaml
      - name: Checkout released revision
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.release.tag_name || github.ref }}
```
Evidence: `.github/workflows/docs-pages.yml:25`; the chain at `.github/workflows/release.yml:118`

### The external-contributor path is instrumented and the response promise held [GOOD] (severity: medium)
CODEOWNERS auto-assigns a reviewer on open so the under-24h target does not depend on anyone watching a feed, and the welcome bot pre-empts the two things that actually trip first PRs (wrong base branch, red draft CI). On the one outside PR in the sample, the promise held: opened 2026-08-19T10:48Z, human review and merge by 2026-08-20T04:35Z.
```
# Auto-assigns a reviewer the moment a PR opens, so the <24h first-response target does not
# depend on anyone watching the repository feed.
* @sagi5060
```
Evidence: `.github/CODEOWNERS:1`; `gh pr list --state merged --json number,author,reviews` -> PR 359, author `xjcway123`, review by `sagi5060`

### No branch protection and no rulesets on any branch [BAD] (severity: high)
Every gate described above is advisory. Nothing prevents a push straight to `main`, a merge with red CI, or a merge with zero reviews. The whole quality story rests on one person's habits.
```
$ gh api repos/agentdecksdk/agentdeck/branches/dev/protection
{"message":"Branch not protected","status":"404"}
$ gh api repos/agentdecksdk/agentdeck/branches/main/protection
{"message":"Branch not protected","status":"404"}
$ gh api repos/agentdecksdk/agentdeck/rulesets
[]
```
Evidence: the three commands above, run against `agentdecksdk/agentdeck`
Ref: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches

### A CI-touching PR was merged 2m41s before its own CI concluded [BAD] (severity: high)
PR #360 changed workflow permissions. It was created at 10:54:51Z and merged at 10:55:01Z, ten seconds later. Its `pull_request` CI run started at 10:54:55Z and did not finish until 10:57:42Z. The merge decision was made on nothing.
```
PR #360 created 2026-08-19T10:54:51Z   merged 2026-08-19T10:55:01Z
CI run   started 2026-08-19T10:54:55Z  ended  2026-08-19T10:57:42Z
```
Evidence: `gh pr list --state merged --json number,createdAt,mergedAt`; `gh run list --branch fix/first-contribution-comment-permission --json conclusion,event,createdAt,updatedAt`

### 145 of 164 merged PRs carry no recorded review of any kind [BAD] (severity: high)
Of the 19 that do, 15 are `copilot-pull-request-reviewer` (PRs 40 to 72) and 3 are `coderabbitai` (PRs 276 to 290). After PR #290 the bot reviews stop entirely. Exactly one merged PR has a human review by someone other than its author: #359, the outside contribution. `reviewDecision` is empty on every sampled PR, and `mergedBy` is `sagi5060` on all of them.
```
{"total":164,"reviewed":19,"median_mins":31,"under5min":15}
reviewers seen: copilot-pull-request-reviewer x15, coderabbitai x3, sagi5060 x2 (one a self-review)
```
Evidence: `gh pr list --repo agentdecksdk/agentdeck --state merged --limit 200 --json number,author,reviews,reviewDecision,mergedBy`

### The review gate is a skill file, so it leaves no auditable trace [BAD] (severity: high)
`deck-reviewer` is a genuinely good reviewer spec, but it runs off-platform. Nothing on GitHub records that it ran, what it found, or whether findings were addressed, and nothing stops a merge when it did not run. From outside the maintainer's terminal, the review gate is indistinguishable from no gate.
```
$ gh pr list --state merged --limit 200 --json reviewDecision --jq '[.[].reviewDecision]|unique'
[""]
```
Evidence: `.claude/agents/deck-reviewer.md:8` (the spec) against the command above (the record)

### The production Jack backend is a laptop process behind a hand-run tunnel [BAD] (severity: high)
`agentdecksdk.com` is a live, unauthenticated assistant endpoint. It is served by a manually started `uvicorn` on the maintainer's machine, exposed through a Cloudflare tunnel that is also started by hand. There is no supervisor, no health check, no restart-on-crash, no redeploy, and no second instance. The repo variable confirms the site points at it.
```
# AgentDeck's own instance is run from a **dashboard-managed** tunnel instead
tunnel: jack
ingress:
  - hostname: agentdecksdk.com
    service: http://localhost:8100
```
Evidence: `examples/jack/cloudflared.yml:34`; `gh api repos/agentdecksdk/agentdeck/actions/variables` -> `JACK_API_URL=https://agentdecksdk.com`

### The release runbook points at a deploy script that is not in the repository [BAD] (severity: high)
Step 7 of the release skill depends on a worktree and a script that exist only on one machine. Whatever `redeploy.sh` does to the served docs is unversioned, unreviewed, and unreproducible. It also competes with `release.yml`, which already dispatches `docs-pages.yml` for the same tag, so there are two docs-deploy paths and only one of them is in git.
```
7. **Redeploy Docs:** If release worktree exists (`~/prjs/agentdeck-docs-release`), run `./redeploy.sh vX.Y.Z` and verify served output.
```
Evidence: `.claude/skills/release/SKILL.md:21`; `grep -rl redeploy.sh .` -> only the two skill copies, never the script

### The process has a bus factor of one, structurally [BAD] (severity: high)
One CODEOWNER, every merge by the same account, releases cut by hand from a local checkout, the docs redeploy from a local script, and the production assistant on a local machine. Velocity is not the issue. No step of shipping AgentDeck can currently be performed by anyone else.
```
.github/CODEOWNERS:  * @sagi5060
mergedBy on all 164 merged PRs: sagi5060
release SKILL.md:    ~/prjs/agentdeck-docs-release/redeploy.sh
cloudflared.yml:     service: http://localhost:8100
```
Evidence: the four locations above, cross-referenced

### There is no staging or preview environment anywhere [BAD] (severity: medium)
Stated plainly: nothing sits between merge and production. `docs-check.yml` builds the site on a PR and asserts two files exist, then throws the build away. There is no preview URL, no staging deployment, no smoke test against a deployed artifact, and no canary. The first time a docs build is served to a user is the release deploy.
```yaml
      - name: Verify static export
        run: test -f out/index.html
      - name: Verify search index
        run: test -f out/_pagefind/pagefind.js
```
Evidence: `.github/workflows/docs-check.yml:46`; no deploy step in that file, and `docs-pages.yml` triggers only on `release: published` or dispatch

### CONTRIBUTING's setup line installs a narrower extra set than the gate needs [BAD] (severity: medium)
A contributor who follows CONTRIBUTING gets `[dev,serve]`. `make install` and CI both use `[dev,serve,durability,observability]`. The consequence is the exact failure mode that #33 and #142 were about: the guarded tests skip, `make check` reports green, and nothing locally hunts skips. The repo built a CI guard for this and left the on-ramp pointing at the trap.
```bash
uv venv && uv pip install -e ".[dev,serve]"      # CONTRIBUTING.md:41
uv pip install -e ".[dev,serve,durability,observability]"   # Makefile:7 and ci.yml:78
```
Evidence: `CONTRIBUTING.md:41` against `Makefile:7` and `.github/workflows/ci.yml:78`

### The `serve` extra is the hole in the anti-skip guard [BAD] (severity: medium)
The importable-extras step checks four packages and not `fastapi`. Six test files skip on a bare `pytest.importorskip("fastapi")` with no reason string, so they also miss the skip-hunt regex, which only matches `needs the [x] extra`. If `serve` silently fails to install, the entire HTTP/SSE surface plus the golden wire suite drops out and both guards stay quiet.
```
ci.yml:83   import langgraph.checkpoint.sqlite, langfuse, psycopg, redis   # no fastapi
tests/golden/conftest.py:14   pytest.importorskip("fastapi")
tests/test_serve.py:15        pytest.importorskip("fastapi")
```
Evidence: `.github/workflows/ci.yml:83`; `grep -rn importorskip tests/` -> 6 bare `fastapi` calls, 1 with a reason string

### "CI runs exactly this" is false in both directions [BAD] (severity: medium)
CONTRIBUTING and the welcome bot both tell contributors that CI runs `make check`. `make lint` covers `examples/`; CI's lint step does not, so a lint error in `examples/` passes CI and fails locally. CI also adds four steps `make check` has no equivalent for: the binary check, the extras-importable check, the skip-hunt, and the second-process golden replay. A contributor with a green `make check` has not run the gate.
```
Makefile:17   ruff check agentdeck/ tests/ examples/
ci.yml:85     ruff check agentdeck/ tests/
```
Evidence: `CONTRIBUTING.md:55` and `.github/workflows/first-contribution.yml:71` against `Makefile:17` versus `.github/workflows/ci.yml:85`

### No `timeout-minutes` on any job, and one CI run burned six hours [BAD] (severity: medium)
The maximum CI wall clock in the last 300 runs is 360 minutes, which is GitHub's default job ceiling: something hung and ran until the platform killed it. Against a 2-minute median, a 10-minute cap would have failed it fast and cost nothing.
```
$ grep -rn 'timeout-minutes' .github/workflows/
(no matches)
CI: median 2 min, max 360 min
```
Evidence: `grep -rn timeout-minutes .github/workflows/` returns nothing; `gh run list --limit 300` aggregate -> `"max":360`

### `requires-python = ">=3.12"` but the gate only ever runs 3.13 [BAD] (severity: medium)
Both CI and the release gate pin `python-version: "3.13"` with no matrix. The package advertises 3.12, ruff targets `py312`, and `deck-dev` is even instructed to create its worktree venv with `--python 3.12`, so the version most likely to be used by an agent contributor is the one never tested.
```toml
requires-python = ">=3.12"     # pyproject.toml:7
target-version = "py312"       # pyproject.toml:86
```
Evidence: `pyproject.toml:7` against `.github/workflows/ci.yml:69` and `.github/workflows/release.yml:55`; `.claude/agents/deck-dev.md:24` uses `--python 3.12`

### Release bumps and config edits land on `dev` with no PR and no review [BAD] (severity: medium)
With no branch protection, some changes skip the PR flow entirely. `chore(release): v4.0.X` commits are direct pushes by design, and `Update context7.json` (which controls how an external documentation service indexes the project) went straight to `dev` with no PR, no CI-gated review, and a subject that says nothing.
```
$ gh api repos/agentdecksdk/agentdeck/commits/3091975/pulls --jq '.[].number'
(empty)
$ gh api repos/agentdecksdk/agentdeck/commits/3c95571/pulls --jq '.[].number'
(empty)
```
Evidence: the two commands above; `3091975 Update context7.json`, `3c95571 chore(release): v4.0.5`

### Four majors in 24 days: SemVer is honest, and that is the problem [BAD] (severity: medium)
v0.1.0 on 2026-07-26, v1.0.0 the next day, v4.0.5 on 2026-08-19. The versioning is scrupulous: each major really did break the surface. The signal a user reads from `4.0.5` at 25 days old is that no API has ever survived a fortnight, which is the opposite of what a "production runtime" wants to say. A long `0.x` would have carried the same information at a lower cost.
```
v0.1.0 2026-07-26    v2.0.0 2026-08-06
v1.0.0 2026-07-27    v3.0.0 2026-08-11
v1.2.1 2026-08-03    v4.0.0 2026-08-16 ... v4.0.5 2026-08-19
```
Evidence: `git for-each-ref --sort=creatordate --format='%(refname:short) %(creatordate:short)' refs/tags`
Ref: https://semver.org/

### CHANGELOG compare links stopped being maintained at 4.0.1 [BAD] (severity: low)
Fifteen link refs for 21 versions. `[Unreleased]` still compares against `v4.0.1` while v4.0.5 is shipped, and 4.0.2 through 4.0.5, 3.0.0, 3.0.1 and 3.1.0 have no ref at all. The release skill says to add compare links; the release workflow's extractor stops at the first line beginning with `[`, so it never notices they are missing.
```
[Unreleased]: https://github.com/agentdecksdk/agentdeck/compare/v4.0.1...HEAD
[4.0.1]: https://github.com/agentdecksdk/agentdeck/compare/v4.0.0...v4.0.1
[4.0.0]: https://github.com/agentdecksdk/agentdeck/compare/v3.1.0...v4.0.0
[3.0.0b1]: ...
```
Evidence: `CHANGELOG.md:2312`; `.claude/skills/release/SKILL.md:15`

### CHANGELOG sections are dev narrative at the length CONTRIBUTING forbids [BAD] (severity: low)
CONTRIBUTING says entries are release notes for a user of the package, not dev narrative. The 4.0.0 section runs 366 lines and the file is 2,326 lines for a 25-day-old project. Attached to a GitHub Release as-is, per the workflow's design, that is not a release note.
```
## [4.0.0] - 2026-08-16     (CHANGELOG.md:109)
## [3.1.0] - 2026-08-13     (CHANGELOG.md:475)
```
Evidence: `CHANGELOG.md:109` to `:474` against `CONTRIBUTING.md:66`

### CONTRIBUTING describes a merge policy the repo does not follow [BAD] (severity: low)
Two claims, both false today. `main` has 16 merge commits including `Merge dev into main for v4.0.5`, so it is not fast-forwarded. And the three most recent substantive PRs (#349, #356, #358) landed as merge commits, not squashes, despite `ship-issue` step 6 specifying `gh pr merge --squash`.
```
- **`main`**  -  release branch. Only fast-forwarded from `dev` when cutting a release.
- PRs are **squash-merged**, so a merged branch's tip never becomes an ancestor of `dev`.
```
Evidence: `CONTRIBUTING.md:22`; `git log origin/main --merges --oneline` -> `d5be96e Merge dev into main for v4.0.5`, `61abb7d Merge pull request #358`

### No dependency caching in the Python gate [BAD] (severity: low)
`astral-sh/setup-uv@v5` is used without `enable-cache`, so every one of the 146 CI runs resolves and downloads the full dependency set including four extras. The Node workflows do cache. Cheap to fix, and the only reason it does not hurt more is that uv is fast.
```yaml
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.13"
```
Evidence: `.github/workflows/ci.yml:67`; `grep -rn enable-cache .github/workflows/` returns nothing

### `ci.yml` has no `permissions` block and no `concurrency` group [BAD] (severity: low)
Every other workflow declares both. CI declares neither, so its token scope is whatever the repository default happens to be, and a second push to a branch does not cancel the first run. The other four workflows show the author knows the pattern; this file was just missed.
```
docs-check.yml:13   permissions:      docs-check.yml:16   concurrency:
docs-pages.yml:8    permissions:      docs-pages.yml:13   concurrency:
release.yml:16      permissions:      release.yml:        (none)
ci.yml:             (neither)
```
Evidence: `grep -n 'permissions\|concurrency' .github/workflows/*.yml`

### Actions are pinned to mutable tags, not commit SHAs [BAD] (severity: low)
`actions/checkout@v4`, `setup-uv@v5`, `deploy-pages@v5`, `pypa/gh-action-pypi-publish@release/v1`. Dependabot bumps them, which is good, but a tag is repointable and `release/v1` is a moving branch. The `pypi` job is the one that holds `id-token: write`, which makes it the worst place in the repo for a floating ref.
```yaml
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```
Evidence: `.github/workflows/release.yml:152`
Ref: https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#using-third-party-actions

### The em-dash ban is stated three times and violated in 23 files [BAD] (severity: low)
CLAUDE.md, `coding-agents.md`, and a dedicated sweep commit all forbid the character. It survives in 23 files, including four of the five workflows, the Makefile, `.pre-commit-config.yaml`, `.gitattributes`, and `tests/golden/README.md` (20 occurrences). The 2026-08-18 sweep covered prose and skipped dotfiles and code. A rule this absolute with no gate step is a rule that only applies to whoever last read it.
```
$ grep -rlP '\x{2014}' --exclude-dir=.git . | wc -l
23
.github/workflows/ci.yml   .github/workflows/release.yml   Makefile
.pre-commit-config.yaml    .gitattributes                  tests/golden/README.md
```
Evidence: `CLAUDE.md:22`, `docs/engineering/coding-agents.md:23`, sweep commit `4134f1d`, against the grep above

### The docs generator has to be run twice to converge [BAD] (severity: low)
A generator that needs two passes is not idempotent, and the runbook papers over it instead of fixing it. Nothing in CI verifies that the five generated docs files match the code, so a forgotten regeneration ships silently.
```
   - Run `python scripts/generate_docs_reference.py` twice.
```
Evidence: `.claude/skills/release/SKILL.md:16`

### The release workflow's PyPI environment URL names a project that does not exist [BAD] (severity: low)
The distribution is `agentdeck-sdk`; the deployment link on every release run points at `pypi.org/project/agentdeck/`. Harmless mechanically, but it is the link a maintainer clicks to confirm a publish landed.
```yaml
    environment:
      name: pypi
      url: https://pypi.org/project/agentdeck/
```
Evidence: `.github/workflows/release.yml:144` against `pyproject.toml:2` (`name = "agentdeck-sdk"`)

### 167 commits carry AI attribution trailers, and one leaks a session URL [BAD] (severity: low)
The no-trailers rule landed on 2026-08-15 and has been honored perfectly since: the last `Co-Authored-By: Claude` on `dev` is dated 2026-08-14. So this is not a live violation. It is unscrubbable history: 167 of 288 `dev` commits (206 across all refs) carry the trailer, one commit is authored by `Claude <noreply@anthropic.com>` outright, two carry the "Generated with" footer, and one exports a private session URL into the permanent record.
```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SMDdgTqsnmU1n4usAiAtuv
```
Evidence: commit `60b95b6`; `git log origin/dev --format='%(trailers:key=Co-authored-by,valueonly)' | grep -ic anthropic` -> 167; rule introduced in `30c3360` (2026-08-15)

### Issue and traceability hygiene is the weakest part of an otherwise tidy board [BAD] (severity: low)
41 of 181 issues carry no label at all, against a purpose-built taxonomy of 32. Only 99 of 158 human-authored merged PRs reference an issue with a closing keyword, so a third of the work has no issue trail. `blocks-beta` is defined as "must be fixed before v3 ships as a public beta" and has zero open issues while v4.0.5 is shipped.
```
issues: {"total":181,"open":51,"closed":130,"unlabeled":41,"gfi_all":17,"finding":36}
PRs:    {"human":158,"human_with_closes":99}
```
Evidence: `gh issue list --state all --limit 400 --json labels`; `gh pr list --state merged --limit 200 --json body,author`; `gh label list`

## Bottom line

The automation in this repository is better than most funded teams manage: the CI gate reasons about its own blind spots, the release path is idempotent and uses trusted publishing, and the agent pipeline separates implementation from review with genuinely good specs for both. What is missing is enforcement, and its absence is total: with no branch protection, no rulesets, a single CODEOWNER who merges everything, and a review gate that lives in a terminal rather than on the platform, every one of those excellent gates is a courtesy the author extends to himself, as PR #360's 2m41s-early merge demonstrates. Fix that in an afternoon by turning on required checks and a required review on `dev` and `main`, then move the production Jack process and the docs redeploy off one laptop, and the process would match the engineering.
