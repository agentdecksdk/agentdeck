# Attack phase

Go after the claims written down in phase 1. Correctness before craft.

## What to trace

- **Lifecycle transitions:** every state the runtime can be in after this change, and whether an invalid one can be expressed. `docs/engineering/runtime-contracts.md` is the law; a state diagram that admits a state the code cannot handle is a BLOCK.
- **Event ordering:** does a new event kind or a reordered append change what a downstream reader (roll-up, cascade, resume) sees.
- **Persistence guarantees:** does the change survive a process boundary (store close/reopen), not just an in-memory run.
- **Streaming semantics:** partial results, cancellation mid-stream, backpressure.
- **Concurrency and cancellation paths:** what happens when a cancel, a timeout, or a second caller lands between the lines this PR touched.
- **Import law:** `import-linter` already gates 3-ring boundaries in `make check`; read its count, do not re-derive it by hand.
- **Invalid states impossible to express:** the PR's own claim, if it makes one. Try to construct the state it says cannot happen.

## Mutation testing

The PR calls something "covered." Prove it or don't claim it:

1. Revert the fix (or the guard, or the boundary check) to the pre-PR code.
2. Run the test the PR names as covering it.
3. It must die, on the expected line, with the expected error. If it passes anyway, the coverage claim is false: that is a BLOCK on the test, not the fix.
4. Run `make check` clean again afterward against an empty `git status`. A mutation probe that leaves the tree dirty invalidates every result after it.

If a probe cannot be run without contaminating shared state (a store, a running process), say so and state the limit: what the probe did check, and what it did not.

## Edge cases live here

Edge cases are not a separate later pass. They are how a claim gets attacked: the empty catalog, the zero-length list, the already-cancelled run, the second concurrent caller. Do not defer them to Craft or Scope.
