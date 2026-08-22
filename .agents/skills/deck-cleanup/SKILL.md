---
name: deck-cleanup
description: Audit and remove one kind of AgentDeck codebase entropy with evidence, isolated work, and narrow changes. Use for narrative prose, dead code, duplicate helpers, or pattern drift; not feature work or mixed refactors.
---

# Deck Cleanup

Remove entropy without changing unrelated behavior.

## Scope

- Classify findings as narrative prose, dead code, duplicate helpers, or pattern drift.
- A broad audit may inspect every category, but each category gets a separate branch and review path.
- Inspect active branches and worktrees before choosing files. Avoid paths under concurrent development.
- Work in an isolated worktree and preserve unrelated user changes.
- For an interactive cleanup, propose and apply one small block at a time.

## Evidence

### Narrative prose

Run `uv run scripts/slopcheck.py --all <file>` on one file at a time. The tool does not replace manual review.

Delete prose when the code remains clear. Keep only concise public contracts or non-obvious rationale involving correctness, concurrency, security, compatibility, or external-system behavior. Each source file keeps one focused top-level description.

Read [references/narrative-prose.md](references/narrative-prose.md) for the deletion and detection rubric.

### Dead code

Build the symbol map with `uv run scripts/repomap.py`, then verify references outside the definition's module and tests. A symbol is removable only after checking dynamic registration, exports, compatibility migrations, and framework discovery.

### Duplicate helpers

Require evidence of the same responsibility, not merely similar syntax. Prefer the architectural owner named by `docs/engineering/architecture.md`. Do not centralize trivial provider-local helpers or create adapter-to-adapter dependencies.

### Pattern drift

Cite the binding engineering rule and the canonical implementation. Behavior changes require contract tests and a branch separate from prose cleanup.

## Change discipline

- Prefer deletion over rewriting.
- Keep issue history, removed designs, review arguments, and changelog narration out of code.
- Add `docs/patterns` guidance only for a repeated implementation shape not already covered by engineering standards.
- Run focused checks after each file and `make check` before completion.
- Use a read-only reviewer when an independent audit is requested. The reviewer names patterns and evidence but does not edit.
- Commit, push, and open a PR only with explicit authorization for each action.
- Nothing found means no change. Report the scanned scope and evidence.
