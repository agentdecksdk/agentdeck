# agentdeck

Declarative harness over the OpenAI Agents SDK + LangGraph, rebuilt as a small
engine-agnostic core (one event schema, one Runtime, pluggable engine and
protocol adapters) — see `docs/project-brief.md` for the why.

**Start at `docs/00-project-index.md`** — it maps every design/delivery doc,
says which one wins when they disagree, and lists what's next.
**All coding standards live in `docs/coding-standards.md`** (typing, errors,
async/event-path law, test structure, naming, dependencies, security, PR/commit
discipline, agent-specific rules). This file is deliberately short and does not
restate them — read that file before writing any non-trivial code.

## Where things stand

**v3 is the live surface, and v1 is gone.** `App`, `agentdeck/agents/`,
`agentdeck/workflows/` and the sandbox/skill-runtime machinery were deleted in
#164 and #71 — if a doc still describes them, the doc is stale, not the tree.

- **`agentdeck/deck.py`** — `Deck`, the one composition root. Two front doors,
  one catalog underneath: `Deck(agents=…, workflows=…, skills=…, mcp=…)` and
  `Deck.from_project()`, which discovers the same four arguments from
  `./.agentdeck/` (`agents/<bundle>/agent.py`, `workflows/<bundle>/workflow.py`,
  `skills/*/SKILL.md`). Lifecycle is
  `NEW → build() → BUILT → (async with) → OPEN → CLOSED`, and CLOSED is terminal.
- **`agentdeck/authoring/`** — declarations (`Agent`, `Workflow`, nodes,
  skills) compiled to an `InvocableSpec`. **`agentdeck/core/`** — event schema,
  content blocks and ports. **`agentdeck/runtime/`** — execution.
  **`agentdeck/adapters/`** — engines, stores, control, telemetry, MCP.
- **The v1 HTTP/SSE wire is still frozen byte-for-byte**, served by
  `surfaces/serve/compat.py` and enforced by `tests/golden/`, replayed on every
  `make test`. That contract outlived the code that first produced it.
- **What's left before `v3.0.0`:** `docs/delivery/roadmap-v3.md` — it holds the
  waves, the rulings taken, and a relevancy verdict per open issue.

Design of record: `docs/design/agentdeck-v2-architecture.md` and
`docs/design/adr-d5-two-stores.md` (both still name the effort "v2"; the layout
and import law they describe are what shipped). Layout and import law also in
`docs/coding-standards.md` §3.

## Architecture rules

- agentdeck owns **configuration only** — settings, discovery, runner glue,
  graph compilation. Execution stays in the Agents SDK / LangGraph. Don't move
  execution logic into agentdeck.
- An agent's contract with a skill is SKILL.md prose + `key=value` stdout lines.
  Never import a skill's typed schema module from agent code.
- Model calls never mutate external state directly; deterministic code does.
- A node calling `interrupt()` re-runs **from its start** when the workflow
  resumes — everything before the `interrupt()` call executes twice. Interrupt
  nodes must be pure; side effects (external mutations, sent messages) belong in
  earlier nodes. Interrupts require `durable = True` (a checkpointer).
- **Sandboxing is not part of v3** (#163, deferred). Nothing is sandboxed, no
  context crosses a process boundary, and the scaffolding for it was deleted —
  don't reintroduce a port for it without that ruling being revisited.

## v3 non-negotiables

(Full detail, incl. typing/errors/async/tests/naming: `docs/coding-standards.md`.)

- `core/` imports **stdlib + pydantic only**, no exceptions — enforced by
  `import-linter` (`.importlinter`, `make lint-imports`).
- Events go through payload classes, never hand-built dicts; consumers use
  `Event.model_validate` and tolerate `UnknownEvent`. New kinds/envelope changes
  land only in dedicated schema PRs (`docs/coding-standards.md` §7).
- Golden JSON snapshots (`tests/core/snapshots/`) change only with an explicit,
  PR-declared schema change.

## Simplicity

- YAGNI first: no interface with one implementation, no config for a value that
  never changes, no scaffolding "for later." Stdlib/native before a dependency;
  a dependency before vendoring. Boring and short beats clever — a coding agent
  should take the first solution that actually works, not the most extensible one.
- This applies *inside* `docs/coding-standards.md`'s judgment-ledger process, not
  instead of it: a deliberate shortcut still gets a one-line ledger entry (or a
  `# ponytail:`-style comment naming the ceiling and the upgrade trigger), it just
  shouldn't be gold-plated in the first place.
- Exceptions the ladder never applies to: input validation at trust boundaries,
  error handling that prevents data loss, the event/session invariants in
  `docs/coding-standards.md` §6–§7, and anything the task explicitly asked for.

## Conventions

- Python ≥3.12, ruff + `ty` (config in `pyproject.toml`), line length 120.
- **`make check` is the gate** (lint + typecheck + lint-imports + test) — CI runs
  exactly this. Pre-commit hooks (ruff, ruff-format, ty, hygiene) installed via
  `pre-commit install`; keep the ruff-pre-commit rev in sync with the venv's
  ruff version or the hook and `make lint` disagree.
- `ty: ignore` only at deliberate SDK shims, with a comment.
- Golden/goldens (`tests/golden/`, `tests/core/snapshots/`) never auto-update;
  `make golden` regenerates them deliberately, with a PR justification.
- Layered pydantic-settings; env prefixes are `AGENTDECK_*` (renamed from the
  original project's `SYSAGENT_*` — never reintroduce the old prefix). Exception:
  a variable a third-party SDK reads natively keeps its own name — `OPENAI_*`,
  `TAVILY_*` — since prefixing it would make an operator set the same value twice.
  Everything agentdeck owns is `AGENTDECK_*`, with no exceptions beyond that list.
- Compose runs the package as an installed dependency, `.agentdeck/` mounted
  read-only.
- `openai==2.32.0` is pinned to `openai-agents==0.17.0` — don't loosen it
  (openai 2.33+ crashes the run loop).
- Add CHANGELOG entries under **Unreleased** with every user-visible change.

## Git / release

- Remote: `github.com/sagi5060/agentdeck` (public). **`dev` is the default
  branch** — PRs and day-to-day commits target `dev`. `main` is release-only.
- **Open the PR as a draft on your first commit, then push as you work.** Not at
  the end: CI then runs while the slice is still in your hands instead of after a
  review round, and a run that dies (quota, API error, a removed worktree) leaves
  a resumable branch rather than work stranded locally. Flip it with `gh pr ready`
  only once your own `make check` is green and the body — including the judgment
  ledger — is complete; if you end up blocked, leave it draft and say so. A red
  draft mid-work is expected and is not a signal.
- Release: bump `pyproject.toml` version + move Unreleased CHANGELOG entries
  on `dev`, merge to `main`, tag `vX.Y.Z` — release.yml verifies the tag
  matches the version, runs the gate, and publishes a GitHub Release.
- `AGENTS.md` just points here — this file is the single source of agent
  instructions.
