# AgentDeck Engineering

This directory contains AgentDeck's engineering law.

Read these in order:

1. [`principles.md`](./principles.md)  -  why we build AgentDeck this way.
2. [`coding-standards.md`](./coding-standards.md)  -  the binding front door for every code change.
3. Specialized documents only when the change touches that area.

## Specialized standards

- [`architecture.md`](./architecture.md)  -  dependency direction, boundaries, adapters, surfaces.
- [`runtime-contracts.md`](./runtime-contracts.md)  -  run lifecycle, events, control, concurrency, liveness.
- [`testing.md`](./testing.md)  -  determinism, contracts, races, recovery, goldens.
- [`dependencies.md`](./dependencies.md)  -  dependency policy and binary assets.
- [`repository-policy.md`](./repository-policy.md)  -  PRs, reviewability, judgment records, change discipline.
- [`coding-agents.md`](./coding-agents.md)  -  additional rules for coding agents.
- [`import-boundaries.md`](./import-boundaries.md)  -  current approved external import exceptions.

Design rationale and historical decisions belong under `docs/design/` and ADRs, not in the standards themselves.

## Precedence

When guidance conflicts:

1. CI-enforced compatibility and safety contracts.
2. Product philosophy and engineering principles.
3. Accepted architecture decisions / ADRs.
4. `coding-standards.md`.
5. Specialized engineering standards in this directory.
6. Existing local repository convention.
7. Normal Python practice.

A lower-level convention must not override a higher-level product or architectural invariant.
