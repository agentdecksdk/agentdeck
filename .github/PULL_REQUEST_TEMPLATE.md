## What

<!-- One or two sentences: what does this PR do? -->

## Why

<!-- Link the issue, or explain the motivation. -->

## Reuse analysis

<!-- Required when adding public symbols: existing abstractions considered (uv run scripts/repomap.py) and why each is insufficient. -->

## Concept budget

<!-- Required when introducing concepts; CI enforces actual <= declared.
new classes: 0
new public symbols: 0
new modules: 0
new dependencies: 0
-->

## Analog

<!-- When adding new files: the existing file this was modeled on, one line on what was matched. -->

## Checklist

- [ ] `make check` passes (lint + typecheck + lint-imports + tests)
- [ ] New behavior has a test
- [ ] CHANGELOG entry under **Unreleased** for every user-visible change
- [ ] User-visible behavior changes update the affected `docs-site/` pages in this PR
- [ ] Unchanged pages in the docs impact report were reviewed
- [ ] Implementation diverging from a design doc amends that doc with a dated note in this PR
- [ ] Goldens (`tests/golden/`, `tests/core/snapshots/`) unchanged  -  or the change is declared and justified here
- [ ] Deliberate simplifications/shortcuts are marked with a comment naming the ceiling
- [ ] Comments are short, self-contained, and never cite doc sections/paragraphs
- [ ] No agent-loop logic added to agentdeck (config only  -  the agent loop stays in the SDK)
