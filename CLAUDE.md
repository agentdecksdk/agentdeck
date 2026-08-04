# agentdeck

Declarative harness over the OpenAI Agents SDK + LangGraph, being rebuilt into a
small engine-agnostic core (one event schema, one Runtime, pluggable engine and
protocol adapters) — see `docs/project-brief.md` for the why.

**Start at `docs/00-project-index.md`** — it maps every design/delivery doc,
says which one wins when they disagree, and lists what's next.
**All coding standards live in `docs/coding-standards.md`** (typing, errors,
async/event-path law, test structure, naming, dependencies, security, PR/commit
discipline, agent-specific rules). This file is deliberately short and does not
restate them — read that file before writing any non-trivial code.

## Where things stand

- **v1 (shipped, frozen behavior):** `agentdeck/agents/`, `agentdeck/workflows/`,
  `agentdeck/skills/`, `agentdeck/runtime/capture.py` — the bundle harness
  described below. Still the live public surface; changes must preserve the
  `.agentdeck/` layout, public API, and SSE wire format byte-for-byte (enforced
  by `tests/golden/`, replayed on every `make test`).
- **v2 (in progress, this branch):** `agentdeck/core/` — event schema and ports —
  is being built per `docs/prompts/pr1-event-schema-prompt.md`, against the
  design in `docs/design/agentdeck-v2-architecture.md` and
  `docs/design/adr-d5-two-stores.md`. Target layout and import law:
  `docs/coding-standards.md` §3. Modules move from v1 to v2 **only** as scheduled
  in `docs/delivery/epic-agentdeck-v2-core.md` — no opportunistic migration.

## v1 architecture rules (current shipped behavior)

- agentdeck owns **configuration only** — settings, capabilities, discovery,
  runner glue, graph compilation. Execution stays in the Agents SDK / LangGraph.
  Don't move execution logic into agentdeck.
- Single entry point: `App` (`agentdeck/app.py`). It always serves the
  `./.agentdeck/` project dir — bundles are `agents/<bundle>/agent.py`,
  `workflows/<bundle>/workflow.py`, `skills/*/SKILL.md`. No other catalog
  mechanism.
- Skill output schemas are **workflow-only**: never import `SkillOutputSchema`
  or a skill's typed schema module from agent code. The agent's contract with a
  skill is SKILL.md prose + `key=value` stdout lines.
- `runtime/capture.py` is the one host↔sandbox wire contract (`CAPTURE_ENV`,
  `Capture`). Both sides import it; don't duplicate the constant.
- `skills/skill_runtime/` is copied into sandbox venvs — keep its imports
  minimal (currently imports `agentdeck.runtime.capture`, a known caveat when
  sandboxing is enabled).
- Model calls never mutate external state directly; deterministic code does.
- A node calling `interrupt()` re-runs **from its start** when the workflow
  resumes — everything before the `interrupt()` call executes twice. Interrupt
  nodes must be pure; side effects (external mutations, sent messages) belong in
  earlier nodes. Interrupts require `durable = True` (a checkpointer).

## v2 in progress — non-negotiables

(Full detail, incl. typing/errors/async/tests/naming: `docs/coding-standards.md`.)

- `core/` imports **stdlib + pydantic only**, no exceptions — enforced by
  `import-linter` (`.importlinter`, `make lint-imports`).
- Events go through payload classes, never hand-built dicts; consumers use
  `parse_event` and tolerate `UnknownEvent`. New kinds/envelope changes land only
  in dedicated schema PRs (`docs/coding-standards.md` §7).
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
  original project's `SYSAGENT_*` — never reintroduce the old prefix).
- Compose runs the package as an installed dependency, `.agentdeck/` mounted
  read-only.
- `openai==2.32.0` is pinned to `openai-agents==0.17.0` — don't loosen it
  (openai 2.33+ crashes the run loop).
- Add CHANGELOG entries under **Unreleased** with every user-visible change.

## Git / release

- Remote: `github.com/sagi5060/agentdeck` (private). **`dev` is the default
  branch** — PRs and day-to-day commits target `dev`. `main` is release-only.
- Release: bump `pyproject.toml` version + move Unreleased CHANGELOG entries
  on `dev`, merge to `main`, tag `vX.Y.Z` — release.yml verifies the tag
  matches the version, runs the gate, and publishes a GitHub Release.
- `AGENTS.md` just points here — this file is the single source of agent
  instructions.
