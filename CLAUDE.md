# middle / agentdeck

Two things live in this repo:

1. **`agentdeck/`** — an installable Python package: declarative harness over
   the OpenAI Agents SDK + LangGraph. This is what we're building right now.
2. **`middle-v1-prd.md`** — the PRD for Middle, an autonomous scheduling
   operator that will be built *on top of* agentdeck.

## Architecture rules (agentdeck)

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

## Conventions

- Python ≥3.12, ruff (config in pyproject), line length 120, tests in `tests/`.
- Layered pydantic-settings; env prefixes are `AGENTDECK_*` (renamed from the
  original project's `SYSAGENT_*` — never reintroduce the old prefix).
- **`make check` is the gate** (ruff + ty + pytest) — CI runs exactly this.
  Pre-commit hooks (ruff, ruff-format, ty, hygiene) are installed via
  `pre-commit install`; keep the ruff-pre-commit rev in sync with the venv's
  ruff version or the hook and `make lint` disagree.
- `ty: ignore` is allowed only at deliberate SDK shims, with a comment.
- Comments: only when a constraint truly isn't visible in the code, and then
  ONE line. No multi-line explanations, no essays above handlers/functions —
  if it needs a paragraph, the paragraph belongs in the PR description.
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
