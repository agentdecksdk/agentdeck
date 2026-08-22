# Narrative prose rubric

## Deletion test

Remove a comment or docstring when identifiers, types, control flow, or an adjacent test already express it. Rewrite only when deletion would hide a contract or non-obvious reason.

Keep:

- public behavior and failure contracts,
- concurrency and atomicity invariants,
- security or privacy rationale,
- compatibility constraints,
- surprising database, SDK, protocol, or filesystem behavior.

Cut:

- line-by-line implementation tours,
- issue numbers and superseded design history,
- comparisons to other adapters when the local rule stands alone,
- statements that label code obvious, important, correct, or load-bearing,
- repeated explanations owned by a public method or binding design document,
- speculative operator advice and future debugging notes.

## AI-slop patterns

Flag the exact line and name the pattern. Do not score the prose or guess who wrote it.

- Binary contrast: `not X, but Y`, `the file, not this process`.
- Interpretive metadiscourse: `the key point`, `this distinction matters`, `the whole reason`.
- Importance puffery: `crucial`, `load-bearing`, `the one corruption`.
- Faux insight: `the honest answer`, `the whole mechanism`.
- Dramatic framing or metaphor where a concrete outcome suffices.
- Robotic repetition of the same rationale across public and private methods.
- Portability failure: prose that could describe any store, adapter, or project unchanged.

Replace labels with the mechanism or consequence. Prefer one or two lines.

## File descriptions

Keep one focused sentence at the top of each source file. State its responsibility, not its history, implementation tour, or relationship to every neighboring module.

## Review format

For each finding report:

1. `file:line`,
2. action: delete, retain, tighten, or move,
3. named pattern or engineering reason,
4. the minimum replacement when tightening.
