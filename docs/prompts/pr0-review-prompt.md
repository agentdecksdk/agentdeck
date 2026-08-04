# Prompt — Reviewing PR #0: Baseline & Guardrails

Copy everything below the line into Claude Code (or any coding agent) with the PR
checked out and `main` available for comparison.

---

You are reviewing PR #0 (`chore: baseline golden files + CI guardrails`) in
`Sagi5060/agentdeck`. This PR is the safety net for an upcoming large refactor: golden
byte-level snapshots of the current SSE/HTTP wire behavior, an import-linter gate, and
CI wiring. **Your review's single purpose is to verify the net has no holes.** A weak
golden suite is worse than none — it will bless a broken refactor with false
confidence — so err toward strictness. Do not review for style, taste, or improvements
outside the PR's stated scope.

Run every verification yourself; do not trust the PR description's claims. Keep a log of
each command and its result — it goes in your review output.

## 1. Scope discipline (cheap checks first)

Run `git diff main --stat` and `git diff main --stat -- agentdeck/`. The diff under
`agentdeck/` must be empty, or contain exactly the one determinism seam the PR
description declares — if a seam exists, read it fully: it must default to current
behavior, be minimal, and be listed under "production files touched." Any undeclared
production change, rename, formatting sweep, or dependency change beyond a pinned
`import-linter` dev-dependency is a **blocker**. Confirm no lockfile churn beyond that
one package.

## 2. Zero behavior change

Run the pre-existing test suite (excluding the new golden/linter additions) on this
branch; it must pass with no modified test files — check `git diff main -- tests/`
touches only added files, never edits to existing tests. An edited existing test is a
**blocker** unless the PR description justifies it convincingly.

## 3. The golden suite — the heart of the review

Verify the net catches what it must catch:

- **Test the net itself (mandatory).** Flip one byte in one committed snapshot; run the
  replay suite; it must fail loudly. Revert. Then temporarily change one character in a
  `serve.py` response path (e.g. a frame separator or field name); the replay suite must
  fail. Revert. If either mutation passes silently, the PR's core deliverable does not
  work — **blocker**.
- **Byte equality, not structural equality.** Read the comparison code: it must compare
  raw bytes, not parsed JSON or dict equality (which would hide formatting, ordering,
  and separator changes — precisely what the serve rewrite could break). Structural
  comparison is a **blocker**.
- **Determinism.** Run the replay suite three times; run the capture procedure twice and
  diff the outputs. Any flake or diff is a **blocker**. Then inspect *how* determinism
  was achieved: the fake model must sit at the SDK boundary with scripted stream items;
  grep the test path for API-key env vars and network clients (`httpx`, `openai`,
  sockets) — none may be exercised.
- **Normalization audit.** Every normalization rule must appear in
  `tests/golden/README.md`, and each must be narrow (a specific field, not a broad
  regex). For each rule ask: could this mask a real regression? A rule that normalizes,
  e.g., all UUIDs anywhere in the body can hide a changed field that happens to contain
  one — flag over-broad rules as **major**. Zero normalizations is the ideal; more than
  a handful suggests the fake model seams are wrong.
- **Coverage.** Snapshots must exist for: agent chat non-streaming, agent chat SSE
  (verify the file contains real `data:` frames, separators, and the terminal frame —
  open it and look), workflow streaming, pending/resume if captured, and health. Missing
  SSE terminal frames or captured-but-empty streams are **major**.

## 4. import-linter gate

Run `lint-imports` yourself — green. Then perform your own red test: add a forbidden
import per the active contract, run again — it must fail; revert. Verify the active
contract is *true and non-vacuous* (the module it constrains actually exists and is
imported somewhere), the staged `agentdeck.core` contract is present but inactive, and —
critically — the linter runs in the **CI workflow file**, not merely locally. Confirm
the PR description contains red-test evidence; absence is **minor** (you have your own),
but a linter not wired into CI is a **blocker**.

## 5. CI and ergonomics

Read the workflow diff: on every PR it must run the existing suite, the golden replay
suite, and the linter. Check `make test` includes the replay suite and `make golden`
exists for deliberate re-capture; re-capture must never run automatically in CI (a
self-updating baseline is not a baseline — **blocker** if present).

## 6. Output format

Produce: (a) a verdict — Approve, or Request Changes; (b) findings grouped by severity
**Blocker / Major / Minor / Nit**, each with file:line, what you observed, how you
reproduced it, and why it matters against the PR's purpose; (c) your verification log —
every command from sections 1–5 with outcomes, including the three mutation tests
(snapshot byte-flip, serve one-char change, forbidden import); (d) a one-paragraph
answer to the only question that matters: *if the future serve rewrite subtly changed
the wire format, would this suite catch it?* — with your evidence.

Request Changes on any blocker. Do not approve with unresolved majors unless the PR
description explicitly defers them with a follow-up issue. If everything holds, say so
plainly and note anything Phase 1 (PR #1, the event schema) should watch for based on
what you saw in the code.
