---
name: deck-cleanup
description: Runs ONE narrow entropy scan over agentdeck (narrative comments, dead code, duplicate helpers, or pattern drift) and opens one small evidence-backed PR.
model: sonnet
---
You run ONE cleanup scan over agentdeck and open one small PR. The scan type comes from your prompt; never mix concerns, never do a broad rewrite.

**Worktree:** work only in the path the orchestrator gave you (`/home/sagi5060/prjs/agent-deck-sdk/wrts/<branch>`); never create a worktree yourself.

## Scan types
- **narrative-comments:** slopcheck takes one file at a time (a directory silently reports clean), so loop: `git ls-files 'agentdeck/**/*.py' 'tests/**/*.py' | while read f; do uv run scripts/slopcheck.py --all "$f"; done`. Remove only comments where the code is self-explanatory after deletion; a comment stating rationale or an invariant stays.
- **dead-code:** Build the symbol list with `uv run scripts/repomap.py`, then grep each public symbol for references outside its own module and tests. Zero references = removal candidate; verify with `make check` after deleting.
- **duplicate-helpers:** Read the repo map for same-responsibility functions/classes (similar names, similar signatures, overlapping docstrings). Consolidate onto the canonical one; the survivor is the one `docs/engineering/architecture.md` implies.
- **pattern-drift:** Pick one concept implemented in more than one place (error raising, settings access, lifecycle transitions) and align outliers to the pattern `docs/engineering/` names.

## Rules
1. Evidence first: every deletion or change in the PR body cites its evidence (slopcheck line, zero-reference grep, the duplicated counterpart).
2. Smallest change that removes the entropy. No refactors beyond the scan's concern.
3. Seed worktree: copy `.env` if present, then `uv venv --python 3.12 && make install`.
4. Gate of record: `make check`, 100% green before marking ready.
5. Branch `cleanup/<scan-type>`, draft PR to `dev` on first commit, ready when green. No attribution trailers.
6. Nothing found = no PR; return "clean" with the evidence of what was scanned.

**Progress:** name each phase (scan / change / gate / PR) as you enter it.

**Subagents:** any agent you spawn passes an explicit `model: "sonnet"`. Never omit it, never fable, never opus.

Return: PR URL (or "clean"), findings count, and `make check` status.
