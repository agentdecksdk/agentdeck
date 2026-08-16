# Testing Standards

**Status:** Binding

Tests protect product guarantees.

## 1. Determinism

Tests should not require:

- real networks,
- API keys,
- real model calls,
- uncontrolled wall-clock behavior,
- uncontrolled random identifiers.

Inject clocks/IDs or use fakes when nondeterminism affects assertions.

A flaky test is a defect.

## 2. Contract suites

Behavior AgentDeck claims across engines/adapters should be expressed as shared contract tests whenever practical.

Do not duplicate the same invariant as unrelated engine-specific tests.

## 3. Golden tests

Use goldens when byte-level or schema-level compatibility is itself the contract.

Golden baselines change deliberately.

CI must never silently regenerate a baseline to make itself pass.

## 4. Race tests

Concurrency guarantees deserve explicit tests.

Examples:

- double resume,
- double answer,
- cancel versus resume,
- competing session admission,
- concurrent key claims.

Arrange contention intentionally.

Assert the promised outcome, not that tasks happened to overlap for a particular duration.

## 5. Recovery tests

Durable guarantees must be tested across restart/recovery boundaries when practical.

Examples:

- terminal state remains terminal,
- sequence/order continues correctly,
- suspended work can be recovered,
- stale ownership does not corrupt active work.

## 6. Failure-path tests

Test hard paths on purpose:

- exceptions during execution,
- cancellation,
- consumer disconnect,
- partial writes,
- adapter failure,
- observer failure,
- store failure.

"Hard to test" is a design signal, not automatically a reason to skip coverage.

## 7. Assertions

Assert behavior and invariants.

Prefer:

```text
exactly one caller wins
```

over:

```text
both callers were inside the critical section at the same time
```

## 8. Names

Test names state the guarantee:

`test_terminal_event_remains_last_after_restart`

not:

`test_case_4`

## 9. No guardrail weakening

Do not:

- loosen assertions,
- modify lint/type configs,
- regenerate goldens,
- skip tests,

merely to make a change green.

A failing guardrail is evidence to understand.
