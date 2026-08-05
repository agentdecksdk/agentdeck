# Contributing to agentdeck

## Branch model

- **`dev`** — default branch. All PRs target `dev`.
- **`main`** — release branch. Only fast-forwarded from `dev` when cutting a release.

## Releasing (x.y.z)

1. Bump `version` in `pyproject.toml` on `dev` and move the **Unreleased**
   CHANGELOG entries under the new version heading.
2. Merge `dev` → `main`.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The release workflow verifies the tag matches `pyproject.toml`, runs the full
   gate, builds sdist + wheel, and publishes a GitHub Release.

## Setup

```bash
git clone https://github.com/sagi5060/agentdeck.git && cd agentdeck
uv venv && uv pip install -e ".[dev,serve]"
pre-commit install          # ruff + ty + hygiene hooks on every commit
```

## Before you push

```bash
make check                  # lint + typecheck + tests — CI runs exactly this
```

## Ground rules

- **agentdeck owns configuration, not execution.** Execution stays in the
  OpenAI Agents SDK / LangGraph. If your change runs things, it belongs in a
  bundle or an app, not here.
- **The `App` entry point serves `./.agentdeck/` only.** Don't add alternative
  catalog mechanisms.
- **Typed boundaries**: new public functions carry annotations. `ty` must pass,
  but never by contorting the code — at deliberate SDK shims, or where appeasing
  the checker would make the code smellier, use a narrow `# ty: ignore[rule]`
  with a one-line reason.
- **Comments are short, focused, and rare.** Only where the code is genuinely
  hard to follow — a non-obvious path, a decision, a key invariant. A comment
  stands alone in its own words: never point at a doc section, paragraph, or
  bullet.
- **Every non-trivial change lands with a test.** No frameworks beyond pytest.
- **New dependencies need a reason** the stdlib or an existing dep can't cover.
- Optional integrations (Langfuse, MCP) must degrade gracefully when
  unconfigured — never crash an unconfigured run.
