
# AgentDeck — Coding Standards

**Status:** binding for all v2 work · **Date:** 2026-08-04 · **Doc #9 in `00-project-index.md`**
Every handoff prompt references this file instead of restating rules. Where this document
and a merged linter/CI config disagree, CI wins and this file gets fixed in the same PR.
Where this document is silent, follow the existing repo pattern; if there is none, choose
the most boring option and record it in the PR's judgment ledger (§13).

---

## 1. Scope and precedence

These standards apply to all new code under `agentdeck/` and `tests/`. Precedence order
when rules collide: (1) CI-enforced checks (ruff, ruff-format, import-linter, contract suite),
(2) architecture decisions D1–D10 and ADR-D5, (3) this document, (4) existing repo
conventions, (5) general Python idiom. Legacy modules being migrated are brought up to
standard **when moved**, never via drive-by edits in unrelated PRs.

## 2. Tooling and formatting

Use the repo's existing toolchain exactly: **ruff** and **ruff-format** with the committed
configs, **pre-commit** hooks installed, **pytest** via `make test`, Python **3.12+** (`requires-python = ">=3.12"`).
Never introduce a new formatter, linter, or test framework. Never run a formatting sweep
across files a PR does not otherwise touch — diff reviewability is the project's primary
QA mechanism, and sweeps destroy it. Line length, quote style, and import sorting are
whatever the committed configs say; arguing with the formatter is out of scope, always.

## 3. Layout and import law

Target layout is design doc §6. The dependency rule is enforced by import-linter but is
broader than the linter's contracts:

- `core/` imports **stdlib + pydantic only**. No exceptions, ever (linter-enforced).
- `runtime/` imports `core/` only (plus stdlib). It never imports adapters directly —
  adapters arrive via the composition root (`app.py`).
- Each `adapters/<family>/<name>/` imports `core/` **plus exactly one external system**.
  `adapters/engines/openai_agents/` is the only place `agents` may be imported;
  `adapters/engines/langgraph/` the only place `langgraph` may be. No adapter imports
  another adapter directory. The working test: *deleting any adapter directory must
  break nothing outside it.*
  *(Amended 2026-08-05, #78: `adapters/tools/mcp/` is the one other holder of `agents` — an
  MCP server has to be an SDK object to be attachable to an SDK agent. It imports the MCP
  client and nothing else of the SDK, and `agents.mcp` is banned everywhere else, by ruff
  TID251 rather than import-linter, which cannot name an external subpackage.)*
- `surfaces/` import `runtime/` and `core/` (and their own framework, e.g. FastAPI in
  `surfaces/serve/`), never adapters directly.
- `authoring/` imports `core/` only; user-facing API compiles to `InvocableSpec`.
- Absolute imports only within the package. No wildcard imports. No import-time side
  effects (no I/O, no client construction at module import).
- `__init__.py` is always just re-exports: a one-line docstring, `from ... import ...`,
  and `__all__` — never class or function definitions. Implementation lives in named
  modules (`store.py`, `engine.py`, `port.py`), however small the package.

## 4. Typing

Full type annotations on every function and method — no bare `def f(x):` anywhere in new
code. `core/` and all port signatures are **contracts**: their annotations are exact,
use `Literal`/`StrEnum` instead of bare strings, and avoid `Any` (an `Any` in a port
signature requires a judgment-ledger entry defending it; `native: Any` on
`InvocableSpec` is the one blessed opaque field — *amended 2026-08-05, #78: and
`ToolSet.tools: tuple[Any, ...]`, for the same reason, engine-native handles core cannot
name; two is the whole list and a third needs the same defence*). Data crossing a boundary is a pydantic
v2 model; process-internal value objects (e.g. `RunContext`) are
`@dataclass(frozen=True, slots=True)`. Mutating a context or event after construction is
a bug by definition — model updates as constructing new objects. `Optional` is written
`X | None`. Prefer `collections.abc` types (`Sequence`, `AsyncIterator`) over concrete
containers in signatures.

`ty` must pass, but never by contorting the code: when satisfying the checker means
casts-of-casts, phantom variables, restructured control flow, or otherwise smellier code
than the straightforward version, keep the straightforward version and add a targeted
`# ty: ignore[rule]` with a one-line reason. A suppression that keeps the code honest
beats an appeasement that obscures it; suppressions are narrow (one line, one rule),
never file- or block-wide.

## 5. Errors and exceptions

`core/errors.py` will own the exception taxonomy (`AgentDeckError` root; `RunCancelled`,
`CapabilityUnavailable`, `InvocableNotFound`, `StoreError`, `EngineError`, …). *Amended
2026-08-05: today that taxonomy is `agentdeck/errors.py`, already covered by its own
import contract; it moves under `core/` when core takes ownership, not before.* The rule
mirrors D10 applied to failures: **SDK and library exception types never cross an
adapter boundary.** Adapters catch external exceptions at the edge and re-raise the core
type with `raise ... from exc` (never swallow the chain). Terminal run failures are also
*data*: they surface as `run.failed` with the closed `error_code` set and `retryable`
flag — the exception is for the caller, the event is for the record, and both must be
emitted consistently. Never catch bare `Exception` except at a top-level loop that
converts it to `run.failed(engine_error)`; never use exceptions for control flow the
status machine already models.

## 6. Async and the event path

All I/O-adjacent code is `async`. Forbidden in any async path: `time.sleep`, blocking
file/network calls, CPU-heavy work without `to_thread`. The event hot path (engine →
Runtime → store → consumer) has additional law: **persist-before-yield** (an event a
consumer has seen is already in the store); **sinks are fire-and-forget** — a slow or
failing sink logs and drops, it never stalls or fails the run (NFR-6); the Runtime is
the **only** assigner of `seq`, one counter per run, recovered from `max(seq)` on
resume. Engine adapters call `await ctx.gate.checkpoint()` between stream items and
before every tool dispatch — a new safe point is a documented contract change, not a
convenience. Use `asyncio.timeout` for deadlines; tasks are created with owners
(no fire-and-forget `create_task` without a supervision/cleanup story).

**Liveness is self-supplied, never borrowed** (issue #87): a component that must make
progress under any conditions creates its own scheduling opportunity — it may never assume
some other component in the same path happens to yield on its behalf. `SinkDispatch.submit`'s
`await asyncio.sleep(0)` on a full queue is the canonical example: the dispatcher's consumer
needs a turn to keep the sink's backlog moving, and the dispatcher supplies that turn itself
rather than trusting the store, the engine, or anything else upstream to suspend first — the
bug this law generalizes was a healthy sink silently losing most of a run because nothing else
in the path happened to yield with the memory store, and did with SQLite only by accident. This
is *not* a rule that every `async def` must contain a suspending `await` — a cache hit, a
buffered write, a batching store between flushes, or a no-op sink is a legitimate
non-suspending coroutine, and forcing a yield into every such call would cost a loop turn per
call for nothing while misdescribing a genuinely fast path. The law is about *dependence*, not
*shape*: nothing may need a yield that only happens to arrive courtesy of a neighboring
component's own implementation detail.

## 7. Events and schema code

The schema in `core/events.py` is governed by D8/D9/D10 (see PR #1 spec — authoritative).
Code-level consequences: new kinds and envelope changes appear **only** in dedicated
schema PRs, never smuggled into feature PRs; every payload class carries its
`kind: Literal[...]` discriminator and a docstring; the golden JSON snapshots under
`tests/core/snapshots/` change only when the PR description explicitly declares a schema
change and its D8 classification (minor vs `v` bump). Producers construct events only
through payload classes — never hand-built dicts. Consumers use `parse_event` and must
tolerate `UnknownEvent` (skip, don't crash); a consumer that pattern-matches on kinds
includes a default case. Engine adapters emit existing kinds or namespaced `custom` —
minting a kind inside an adapter is a review-rejection offense (D10).

## 8. Tests

**Determinism is law (NFR-4):** no network, no API keys, no real model calls, no wall
clock or random ids in assertions — inject clock/id factories or use the scripted fake
model. A flaky test is a P1 bug, not an annoyance. Structure:

- **Contract suite** (`tests/contract/`): every cross-engine invariant lives here,
  parametrized over all engines including the stub. A new invariant discovered anywhere
  is added here, not as a one-off. This suite is LSP made executable — it is the merge
  gate for engine work.
- **Golden suites** (`tests/golden/`, `tests/core/snapshots/`): byte-level, never
  structurally compared, regenerated only deliberately via `make golden` with a PR
  justification. CI never auto-updates a baseline.
- **Unit tests** live beside their subject's test module following existing repo
  patterns; integration tests that need two processes (UC3-style) are marked and run in
  CI, not skipped.
- Race and crash paths are tested on purpose (double-resume, kill-mid-stream,
  crash-between-writes) — "hard to test" is a design smell to report, not a reason to
  skip.
- Test names state the invariant: `test_terminal_event_is_last_after_restart`, not
  `test_workflow_2`.

## 9. Naming

Modules and packages: short, lowercase, no `utils.py` dumping grounds (name the concept:
`invariants.py`, `framing.py`). Events: dot-case `noun.past_tense`. Ports: `<Role>Port`.
Adapters: directory named for the external system. Async iterables read as streams
(`events`, not `get_events_list`). Booleans read as predicates (`retryable`,
`supports_steering`). No abbreviations that save under three characters (`ctx` and
`spec` are blessed by existing usage; invent no new ones).

## 10. Docstrings and decision links

Every public module, class, and port method has a docstring stating *what contract it
implements*, and cites the governing decision where one exists: `"""Execution store for the openai-agents engine (ADR-D5: engine-private working memory)."""`. Comments are
short, focused, and exist only where the code is genuinely hard to follow — a non-obvious
path, a deliberate decision, a key invariant. Everything else stays uncommented: comments
explain *why*, never *what* (the code says what). A comment describes the code in its own
words and stands alone: never point at a doc section, paragraph, or bullet (`# see
milestone §3` is banned — the reader has the code, not the doc open). A decision *name*
(ADR-D5, D10) may appear only with the reason inlined, as in the docstring example above.
TODOs carry an owner and a pointer (`# TODO(sagi): promote to core kind per D10 if usage
recurs`). Docs-as-code: when implementation diverges from a design doc, the same PR
amends the doc with a dated note (index §6 housekeeping rule).

## 11. Dependencies

Adding or upgrading any dependency is its own judgment-ledger entry with a one-line
justification; pin exact versions consistent with the repo's current style. `core/` adds
nothing beyond pydantic, ever. Prefer stdlib over a small dependency; prefer a small
dependency over vendoring; never vendor silently. SDK version bumps (`openai-agents`,
`langgraph`) touch only their adapter directory — if a bump forces edits elsewhere, that
is an architecture violation to report before merging.

## 12. Security and data handling

No secrets in code, tests, fixtures, goldens, or PR descriptions — goldens are scrubbed
by construction because fakes need no keys. Raw tool results and artifacts never inline
in events (preview + hash + reference only, §7 / schema decision 7). `tenant` and
`principal` are never defaulted inside adapters or surfaces — they flow from the
composition root via `RunContext` only. Log lines never include full message content at
INFO level (event ids and kinds, yes; bodies, no). Idempotency keys accompany every
side-effecting tool call once Story 3 lands.

## 13. Commits, PRs, and the judgment ledger

Conventional commits (`feat(core): …`, `chore: …`, `fix(adapters/langgraph): …`). One
concern per PR; soft cap ~500 changed lines excluding snapshots/goldens — if a task
can't fit, slice it, don't waive it (Story 2 has an explicit exemption plan). Every PR
description contains: summary; production files touched; **the judgment ledger** — every
choice the spec or this document didn't dictate, however small (this list is the review
agenda); test evidence for anything CI can't show (red-tests, two-process runs); and doc
amendments made. Force-pushes to shared branches: never. A PR that changes behavior and
a PR that moves code are never the same PR.

## 14. Rules for coding agents (Claude Code et al.)

Handoff prompts cite this file; agents must treat it as binding. Additional agent-
specific law: read the referenced design docs *before* writing code; never expand scope
beyond the prompt ("while I'm here" is forbidden); on discovering a blocker or a
design/reality conflict, **stop and report** with specifics rather than coding around
it; never modify goldens, linter configs, or CI to make a failing check pass — a failing
guardrail is a finding, not an obstacle; produce the judgment ledger as you go, not
reconstructed at the end. Reviewer agents: verify claims by running commands, never by
trusting the PR description (per the PR #0 reviewer prompt's model).

## 15. Enforcement summary

| Rule family             | Enforced by                                                           |
| ----------------------- | --------------------------------------------------------------------- |
| Formatting, lint        | ruff + ruff-format + pre-commit, CI                                   |
| Import law              | import-linter contracts, CI (plus §3 review rules beyond the linter) |
| Schema stability        | golden JSON snapshots + dedicated-PR rule, review                     |
| Engine substitutability | contract suite as merge gate                                          |
| Wire compatibility      | golden SSE replay suite                                               |
| Determinism             | no-network test env, double-run stability checks                      |
| Everything else         | review against this document + the judgment ledger                    |

Amending this document: one PR, one rationale paragraph per change, index row updated.
Standards that are repeatedly waived in ledgers should be amended or defended — a rule
nobody follows is worse than no rule.
