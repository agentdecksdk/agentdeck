# AgentDeck

AgentDeck is a declarative runtime harness for multi-agent systems and workflows (OpenAI Agents SDK + LangGraph).

**Core standards:** `docs/engineering/`  -  the linked suite of binding engineering law:
1. [`docs/engineering/principles.md`](docs/engineering/principles.md)  -  Product philosophy & North Star.
2. [`docs/engineering/coding-standards.md`](docs/engineering/coding-standards.md)  -  Binding front door for every code change.
3. [`docs/engineering/coding-agents.md`](docs/engineering/coding-agents.md)  -  Mandatory rules for coding agents.
4. Specialized standards: [`architecture.md`](docs/engineering/architecture.md), [`runtime-contracts.md`](docs/engineering/runtime-contracts.md), [`testing.md`](docs/engineering/testing.md), [`dependencies.md`](docs/engineering/dependencies.md), [`repository-policy.md`](docs/engineering/repository-policy.md), [`import-boundaries.md`](docs/engineering/import-boundaries.md).

---

## 1. Product Philosophy & North Star

> **Make powerful agentic systems feel obvious to build, compose, run, and operate.**
> **Great software does the hard work so its users do not have to. Simple on the outside, elegant on the inside.**

* **User owns intent, AgentDeck owns machinery:** Users define agents, tools, workflows, skills, and context. AgentDeck manages run identity, execution lifecycle, persistence, event streams, cancellation, and concurrency.
* **Abstractions must delete complexity:** An abstraction is successful only when the caller needs to know *less*. Never leak internal plumbing (stores, resolvers, internal contexts, log keys) into public user APIs.
* **One obvious path first:** One clean, standard path for common tasks (`await deck.run(...)`). Advanced knobs remain escape hatches, never obstacles.
* **Conciseness is mandatory:** No fluff, no sprawling prose. If one sentence suffices, write one sentence. Code, comments, docs, PR descriptions, and issue specs must be terse, precise, and dense with signal.
* **No em dashes:** Never use the ` - ` character in any documentation, code, comments, or agent output. Use a regular hyphen `-`, colon `:`, or separate into distinct sentences.

---

## 2. Architecture & Layout

`Deck` is the single composition root: `Deck(...)` or `Deck.from_project()` (discovering `./.agentdeck/`).

* **`agentdeck/core/`**: Pure domain model, event schema, content blocks, ports, error taxonomy. **Imports stdlib + pydantic only** (enforced by `import-linter`).
* **`agentdeck/runtime/`**: Execution orchestration, lifecycle state machine, event dispatch. Imports `core/` only.
* **`agentdeck/adapters/`**: Pluggable integrations (engines: `openai_agents`, `langgraph`; stores: `sqlite`, `postgres`, `redis`; `mcp`; `telemetry`). Each adapter imports `core/` plus exactly one external technology. Adapters never import other adapters.
* **`agentdeck/authoring/`**: Declarations (`Agent`, `Workflow`, `Skill`) compiled to specs.
* **`agentdeck/surfaces/`**: Ingress surfaces (HTTP/SSE in `surfaces/serve/`, CLI).

---

## 3. Engineering & Coding Standards

* **Correctness before convenience:** Strict internal invariants, forgiving external APIs. Make invalid states impossible to express.
* **Errors are part of the API:** Every error must state what happened, why it happened, and the exact code/action to resolve it.
  * Good (real, `adapters/engines/langgraph/engine.py`): "paused at a node boundary but is durable=False: with no checkpointer the paused run cannot be resumed. Set durable = True on the workflow".
  * Bad: "invalid workflow state".
* **Typing:** Python ≥3.12. Strict annotations everywhere. Pydantic v2 models at system boundaries; `@dataclass(frozen=True, slots=True)` for internal immutable value objects. No unprincipled `Any`.
* **Zero unnecessary abstractions:** YAGNI. Delete dead code aggressively. Do not add configuration for things that never change.
* **Comments:** Extremely rare, max 1–2 lines explaining non-obvious *why*, never restating what the code does.
  * Good (real, `core/control.py`): `# Before the raise, because the raise is what records the effect: an intent left pending behind an honored one would be honored a second time on the next resume.`
  * Bad: `# Increment the retry count` above `retry_count += 1`. A `PostToolUse` hook (`scripts/slopcheck.py`) flags this per edit.

---

## 4. Verification Gate & Conventions

* **Gate:** `make check` (runs ruff, ty typecheck, import-linter, and pytest). Must be 100% green.
* **Settings & Env:** Layered pydantic-settings; all environment variables use the `AGENTDECK_*` prefix exclusively (except third-party SDK vars like `OPENAI_*`).
* **Goldens & Snapshots:** `tests/golden/` and `tests/core/snapshots/` update only via intentional `make golden` with PR justification.
* **CHANGELOG:** Add concise, user-facing entries under `## [Unreleased]` for any user-visible change.

---

## 5. Agent Instructions & Git Discipline

* **Communication conciseness:** Keep text between tool calls to ≤25 words. Keep final responses to ≤100 words unless more detail is required.
* **Branches:** `dev` is default; PRs target `dev`. `main` is release-only.
* **No attribution trailers:** Never include `Co-Authored-By`, `🤖 Generated with`, or AI vendor signatures in commits, PRs, or issues.
* **Draft PR workflow:** Open a draft PR on your first commit (`gh pr create --draft`) and push continuously as you work. Mark ready with `gh pr ready` only when `make check` is clean.
