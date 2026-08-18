# Prompt  -  PR #0: Baseline & Guardrails

Copy everything below the line into Claude Code (or any coding agent) at the repo root.

---

You are working in the `Sagi5060/agentdeck` repository (Python 3.11+, package `agentdeck`
v1.2.1). It is a declarative harness over the OpenAI Agents SDK and LangGraph: agents and
workflows are discovered by convention from `.agentdeck/`, and `serve.py` exposes them
over FastAPI with SSE streaming. A large refactor is planned (three-ring architecture:
core events / engine adapters / thin surfaces). **This PR is the safety net that must
exist before any refactor code is written.** It changes zero production behavior.

## Task

Produce a single small PR titled `chore: baseline golden files + CI guardrails (PR #0)`
containing exactly three things: byte-level golden baselines of the current SSE/HTTP wire
behavior, an import-linter setup wired into CI, and the CI workflow to run both. Nothing
else.

## Before writing code

Read, in this order: `serve.py`, `agentdeck/agents/runners/headless.py`,
`agentdeck/workflows/runners/`, `agentdeck/runtime/sessions.py`, the existing `tests/`
directory (reuse its fixture patterns and any existing fake-model utilities),
`Makefile`, and `pyproject.toml`. Follow existing conventions; do not introduce new test
frameworks or styles.

## Deliverable 1  -  Golden wire baselines (`tests/golden/`)

Capture the current, user-visible wire format so the future serve rewrite can be diffed
byte-for-byte against v1.2.1 behavior.

Requirements:

1. Create a minimal fixture bundle under `tests/golden/fixture_project/.agentdeck/` with
   one trivial agent and one trivial two-node workflow (one interrupt point if the
   existing runner supports exercising it cheaply).
2. **Determinism is the hard requirement.** Stub the model at the SDK boundary: a fake
   model/client that returns a scripted sequence of stream items (text deltas, one tool
   call, completion). No network, no API keys, no real model. Prefer injecting fakes via
   existing seams (env/settings/monkeypatch in tests). If  -  and only if  -  determinism is
   impossible without a production seam (e.g. an injectable id factory or clock), the
   seam must be the minimal possible change, default to current behavior, and be listed
   explicitly in the PR description under "production files touched."
3. Using FastAPI's test client against the real `serve.py` app, record raw response
   bytes for at least: (a) `POST /agents/{name}/chat` non-streaming, (b) the same with
   `?stream=true` capturing every SSE frame including separators and terminal frame,
   (c) streaming workflow run, (d) `GET /pending` after an interrupt plus `POST /resume`
   if the fixture workflow supports it, (e) `GET /health`.
4. Store captures as committed files under `tests/golden/snapshots/`. Write a replay
   test that re-runs each request and asserts **byte equality** against the snapshot.
   If any field is irreducibly variable (run ids, timestamps), prefer pinning it via the
   fake; only as a last resort apply a normalization step, and document every
   normalization rule in `tests/golden/README.md`  -  an undocumented normalization is a
   hole in the safety net.
5. Prove stability: the golden test suite must produce identical results across two
   consecutive full runs (add a CI step or a test that runs the capture twice and
   compares).

## Deliverable 2  -  import-linter guardrails

1. Add `import-linter` as a dev dependency (pin it; change no other dependencies).
2. Add `.importlinter` (or `pyproject.toml` section) with: (a) one contract that is
   enforceable **today** and true  -  e.g. `agentdeck.errors` must not import `agents`,
   `langgraph`, or `fastapi`; verify what actually holds before writing it; (b) the
   prepared future contract for `agentdeck.core` (must not import `agents`, `langgraph`,
   `fastapi`, `redis`), included but disabled/commented with a note that Phase 1
   activates it.
3. Red-test procedure: in a scratch commit, add a forbidden import, show the linter
   failing in CI, revert. Paste the failing log excerpt into the PR description as
   evidence the gate actually bites.

## Deliverable 3  -  CI

Add or extend the CI workflow to run, on every PR: the existing test suite, the golden
replay suite, and `lint-imports`. Also add `make golden` (re-capture snapshots  -  to be
used deliberately, never automatically) and ensure `make test` includes the replay
suite.

## Hard constraints

Zero behavior change and  -  ideally  -  zero diff under `agentdeck/` (the only permitted
exception is the minimal determinism seam of Deliverable 1.2, explicitly justified). No
renames, no formatting sweeps, no dependency upgrades, no new abstractions, no `core/`
package yet  -  the events schema is PR #1, not this PR. Keep the whole PR under roughly
500 changed lines excluding snapshot files. Conventional commit messages.

## Definition of done (verify each before finishing)

- Full existing test suite passes, byte-identical to before this PR.
- Golden snapshots committed; replay test green; double-run stability check green.
- `tests/golden/README.md` explains how snapshots were produced, the fake-model script,
  and every normalization rule (ideally: none).
- import-linter green in CI with the currently-true contract; staged core contract
  present; red-test evidence in the PR description.
- PR description contains: summary, the list of production files touched (target:
  empty), red-test log excerpt, and instructions for re-capturing goldens.

Work through the deliverables in order (goldens are the bulk). If you discover the SSE
frames cannot be made deterministic without touching more than one small production
seam, stop and report the blocker with the specific coupling you found instead of
expanding the diff.
