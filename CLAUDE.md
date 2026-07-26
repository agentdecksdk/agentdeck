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
  `./.agentdeck/` project dir — bundles are `<bundle>/agent.py`,
  `<bundle>/workflow.py`, `skills/*/SKILL.md`. No other catalog mechanism.
- Skill output schemas are **workflow-only**: never import `SkillOutputSchema`
  or a skill's typed schema module from agent code. The agent's contract with a
  skill is SKILL.md prose + `key=value` stdout lines.
- `runtime/capture.py` is the one host↔sandbox wire contract (`CAPTURE_ENV`,
  `Capture`). Both sides import it; don't duplicate the constant.
- `skills/skill_runtime/` is copied into sandbox venvs — keep its imports
  minimal (currently imports `agentdeck.runtime.capture`, a known caveat when
  sandboxing is enabled).
- Model calls never mutate external state directly; deterministic code does.

## Conventions

- Python ≥3.12, ruff (config in pyproject), line length 120, tests in `tests/`.
- Layered pydantic-settings; env prefixes are `AGENTDECK_*` (renamed from the
  original project's `SYSAGENT_*` — never reintroduce the old prefix).
- `make test` / `make lint` / `make build`; compose runs the package as an
  installed dependency, with `.agentdeck/` mounted read-only.

## History / provenance

The package was extracted from `~/prjs/sys-agents-team/SysAgentsHarness`
(package name `sysagent`). The `sysagents_core` dependency never existed as a
real package — its `Capture`/`CaptureActor` were reconstructed into
`agentdeck/runtime/capture.py`. `db/connector.py` optionally talks to
`sysagents-knowledge` (lazy import, off unless configured).
