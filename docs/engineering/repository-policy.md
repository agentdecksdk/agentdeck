# Repository and Change Policy

**Status:** Binding process guidance

The purpose of process is to improve correctness and reviewability, not to create bureaucracy.

## 1. One understandable purpose

A change should tell one coherent story.

Avoid unrelated cleanup and drive-by formatting.

Reviewability is part of correctness.

## 2. Refactor versus behavior

Separate refactoring from behavior change when that materially improves reviewability.

Do not split changes ritualistically when separation would make the work less coherent or less safe.

## 3. Mechanical churn

Do not run broad formatting, import, rename, or cleanup sweeps across files unrelated to the change.

Large mechanical changes should be isolated.

## 4. Judgment record

Record engineering decisions future maintainers need to understand.

Capture:

- meaningful tradeoffs,
- architecture choices,
- intentional deviations,
- unresolved assumptions,
- non-obvious standards interpretation.

Do not record routine choices simply because no spec prescribed them.

The judgment record is a review aid, not a diary.

## 5. PR evidence

A meaningful PR should make it easy to identify:

- what changed,
- why,
- what production behavior is affected,
- what tests prove it,
- what docs/contracts changed,
- what non-obvious judgments were made.

## 6. Draft early when useful

For work that benefits from continuous CI/review visibility, open a draft early and push incremental coherent slices.

A red draft under active development carries no completion signal.

## 7. Guardrails

Never change:

- golden baselines,
- lint rules,
- type rules,
- CI,
- compatibility tests,

merely because they expose a problem in the implementation.

Change a guardrail only when the guardrail itself is intentionally changing.

## 8. Standards maintenance

Standards describe current law.

Do not accumulate dated amendment history inline.

Git records history.

If a standard is repeatedly waived, either:

- the implementation is repeatedly wrong, or
- the standard is wrong.

Resolve the contradiction.
