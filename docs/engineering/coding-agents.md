# Coding Agent Rules

**Status:** Binding for coding agents

Coding agents follow all human engineering standards.

Additional rules:

## Before coding

- Read `principles.md`.
- Read `coding-standards.md`.
- Read specialized standards relevant to the change.
- Read referenced design/ADR material before implementing behavior governed by it.
- Identify the requested scope and applicable invariants.

## During coding

- Stay within scope.
- Do not perform unrelated cleanup.
- Prefer the smallest coherent implementation.
- Do not add abstractions speculatively.
- Do not weaken tests, lint, typing, goldens, or CI to make the implementation pass.
- Do not hide a design/reality conflict with a workaround.

## When blocked

If the requested design conflicts with the repository, runtime invariants, or another binding decision:

> Stop that implementation path and report the conflict precisely.

Do not silently invent a new architecture.

## Testing

Verify claims by running the relevant commands.

Do not trust existing comments, issue text, or PR descriptions when executable evidence is available.

## Judgment record

Record non-obvious engineering judgments as they arise.

Do not reconstruct fake rationale after the implementation is already complete.

## Final standard

Coding agents optimize for:

- correctness,
- simplicity,
- clarity,
- maintainability,
- narrow scope.

Not for:

- maximum abstraction,
- maximum code produced,
- cleverness,
- generalized machinery without a demonstrated need.
