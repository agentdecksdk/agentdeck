## What

<!-- One or two sentences: what does this PR do? -->

## Why

<!-- Link the issue, or explain the motivation. -->

## Checklist

- [ ] `make check` passes (lint + typecheck + lint-imports + tests)
- [ ] New behavior has a test
- [ ] CHANGELOG entry under **Unreleased** for every user-visible change
- [ ] User-visible behavior changes update the affected `docs-site/` pages in this PR
- [ ] Implementation diverging from a design doc amends that doc with a dated note in this PR
- [ ] Goldens (`tests/golden/`, `tests/core/snapshots/`) unchanged  -  or the change is declared and justified here
- [ ] Deliberate simplifications/shortcuts are marked with a comment naming the ceiling
- [ ] Comments are short, self-contained, and never cite doc sections/paragraphs
- [ ] No execution logic added to agentdeck (config only  -  execution stays in the SDK / LangGraph)
