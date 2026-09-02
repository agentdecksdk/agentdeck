# Attack phase

High freedom. Go after the claims written down in phase 1; correctness before craft. This is an
open field, not a checklist: which of the angles below matter depends on what this PR actually
touches. Running all of them on every PR is how the old reviewer produced uniform coverage and
4,000-word reviews. Pick the ones the claims and the diff put at risk, and go deep there instead
of shallow everywhere.

## Angles, not a sequence

- **Lifecycle transitions:** if the change touches runtime state, is there a state the code can
  now reach that `docs/engineering/runtime-contracts.md` (the law) or the code itself cannot
  handle.
- **Event ordering:** would a new event kind or a reordered append change what a downstream reader
  (roll-up, cascade, resume) sees.
- **Persistence guarantees:** does the change survive a process boundary (store close/reopen), not
  just an in-memory run, if that boundary is in play.
- **Streaming semantics:** partial results, cancellation mid-stream, backpressure, when the PR
  touches a stream.
- **Concurrency and cancellation paths:** what happens when a cancel, a timeout, or a second caller
  lands between the lines this PR touched.
- **Import law:** `import-linter` already gates 3-ring boundaries in `make check`; read its count,
  do not re-derive it by hand.
- **Invalid states the PR claims are impossible:** try to construct the one it says cannot happen.
- **The caller's seat:** when the diff changes a public signature or an idiom the docs teach, run the
  doc's own snippet through the type checker rather than reading the annotation. #231 (`TurnResult |
  Any`) read as coherent from inside and did not type-check from outside; two outside reviewers hit
  it and the gate never did, because no shipped example indexes the value the way a user does on
  line one.
- **Docstring truth:** a docstring asserting a guarantee is a claim, so attack it like the others.
  `slopcheck` measures a docstring's size and shape and never reads what it says. #417, #468 and
  #423 each shipped a sentence describing a mechanism nobody built, and a human reading found all
  three.

A claim with a named consequence you actually reproduced is a BLOCK. Skip the angles that do not
bear on this PR's claims rather than writing something about each for completeness.

## Mutation testing

The PR calls something "covered." Prove it or don't claim it:

1. Revert the fix (or the guard, or the boundary check) to the pre-PR code.
2. Run the test the PR names as covering it.
3. It must die, on the expected line, with the expected error. If it passes anyway, the coverage claim is false: that is a BLOCK on the test, not the fix.
4. Run `make check` clean again afterward against an empty `git status`. A mutation probe that leaves the tree dirty invalidates every result after it.

If a probe cannot be run without contaminating shared state (a store, a running process), say so and state the limit: what the probe did check, and what it did not.

## Edge cases live here

Edge cases are not a separate later pass. They are how a claim gets attacked: the empty catalog, the zero-length list, the already-cancelled run, the second concurrent caller. Do not defer them to Craft or Scope.
