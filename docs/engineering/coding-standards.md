# AgentDeck Coding Standards

**Status:** Binding
**Scope:** Production code, tests, examples, and engineering changes.

This is the front door. Read [`principles.md`](./principles.md) first.

These standards intentionally stay small. Deeper rules live in the linked specialized documents.

## 1. Design before implementation

For meaningful changes, reason in this order:

```text
Problem
→ developer experience
→ invariants
→ public behavior
→ failure cases
→ internal model
→ implementation
→ tests
```

Do not let implementation accidents define product behavior.

Prefer the smallest clear solution that preserves the required guarantees.

## 2. Public API

Public APIs represent user intent, not runtime machinery.

- Keep the happy path small.
- Prefer good defaults over required configuration.
- Expose advanced controls as escape hatches.
- Avoid leaking internal ports, storage identities, provider objects, or lifecycle bookkeeping unless they are intentionally part of the product contract.
- Preserve native access where practical; do not create abstraction prison.

## 3. Architecture boundaries

Stable contracts point inward; integrations stay at the edges.

- Core must not depend on concrete engines, stores, telemetry providers, or serving frameworks.
- Runtime coordinates behavior through contracts and ports, not concrete adapters.
- Adapters isolate external SDKs and should not depend on each other.
- Surfaces consume AgentDeck behavior; they do not reimplement runtime semantics.
- Architecture exceptions must be explicit and registered.

See [`architecture.md`](./architecture.md) and [`import-boundaries.md`](./import-boundaries.md).

## 4. Types and contracts

Type boundaries as contracts.

- Annotate all new public and cross-layer functions.
- Use closed types (`Literal`, enums) for closed domains.
- Avoid `Any` in contracts unless the value is intentionally opaque.
- Use explicit schemas for durable/process/protocol boundaries.
- Prefer immutable value objects when mutation is not part of the model.
- Do not distort straightforward code merely to satisfy a type checker; use narrow documented suppressions when necessary.

## 5. Errors

Errors are part of developer experience.

An error should say:

1. what happened,
2. why,
3. what to do next when there is an obvious next step.

Translate external SDK failures at integration boundaries unless native exceptions are intentionally exposed.

Never silently swallow unexpected failures.

## 6. Async, concurrency, and ownership

- Do not block async runtime paths.
- Every spawned task must have an owner, failure story, and shutdown story.
- Do not depend on scheduler timing for correctness.
- Concurrency outcomes must follow explicit invariants.
- A component that requires scheduling progress must provide the scheduling opportunity it depends on.

See [`runtime-contracts.md`](./runtime-contracts.md).

## 7. Runtime invariants

Runtime guarantees are product contracts.

At minimum:

- observed durable events are already persisted,
- terminal means terminal,
- durable ordering has one authoritative owner,
- control happens at explicit safe points,
- observability does not own execution,
- races have defined winners/outcomes,
- recovery preserves the identity and meaning of the run.

See [`runtime-contracts.md`](./runtime-contracts.md).

## 8. Readability

Prefer:

- explicit control flow,
- meaningful domain names,
- small coherent units,
- localized policy,
- predictable ownership,
- standard Python.

Avoid:

- speculative abstractions,
- unnecessary indirection,
- generic dumping-ground modules,
- duplicated policy,
- hidden side effects,
- cleverness that saves lines but costs understanding.

Comments explain **why**, not **what**.

Public APIs get concise contract-oriented docstrings.

## 9. Tests

Tests protect AgentDeck's promises, not implementation accidents.

- Keep tests deterministic.
- Express cross-engine guarantees as shared contract tests where practical.
- Test races, recovery, and failure boundaries intentionally.
- Assert outcomes and invariants, not timing coincidences.
- Name tests after the guarantee they protect.
- A flaky test is a defect.

See [`testing.md`](./testing.md).

## 10. Dependencies

Dependencies must earn their place.

Prefer existing project capability, then stdlib, then a focused dependency.

Keep provider-specific dependencies localized to their integration boundary whenever practical.

See [`dependencies.md`](./dependencies.md).

## 11. Change discipline

A change should have one understandable purpose.

- Avoid unrelated cleanup.
- Avoid formatting files you do not otherwise need to touch.
- Separate refactoring from behavior change when that materially improves reviewability.
- Record non-obvious engineering judgments and deviations, not routine implementation choices.
- Do not weaken tests, lint rules, goldens, or CI merely to make a change pass.

See [`repository-policy.md`](./repository-policy.md).

## 12. Tooling

Committed repo configuration is authoritative for mechanical concerns such as formatting, linting, typing, imports, and test commands.

Do not duplicate tool configuration as prose.

Do not introduce competing tooling without a deliberate project decision.

## 13. Coding agents

Coding agents follow the same standards as maintainers, plus the agent-specific rules in [`coding-agents.md`](./coding-agents.md).

## Final rule

Reserve absolute rules for genuine invariants.

Where several simple and correct solutions exist, use engineering judgment.

If a rule is repeatedly waived, fix the implementation or fix the rule. Do not accumulate permanent exceptions around bad policy.
