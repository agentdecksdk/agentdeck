# Self-review (Stage 3)

LOW freedom. A checklist you can improvise is not a checklist: answer every question below in
writing, against `git diff dev...HEAD`, before Stage 4. CONSTRAINT: an unanswered question fails
the stage.

## The coverage question

CONSTRAINT, answer in writing before the gate:

> What path does this change create, expose, or leave behind that no test exercises? Name it, or
> write the test.

This is the question the other ten do not ask. All four `deck-dev` PRs of the v5.0.0 milestone
tested what they built and not what they changed, and `make check` was green on every one: the
same blind spot covers its `docs impact:` line, which only reflects reality when Stage 4's
`PR_BODY` export ran.

| PR | built and tested | created and not tested |
|---|---|---|
| #415 | a refusal path for bad `ctx.parallel` input | children already running when it fires |
| #418 | an abandonment path | the event log after abandonment (two terminal events shipped) |
| #420 | `_mint` for forked agents | that a minted agent can delegate (`fork("Writer")` raised `NotFoundError`) |
| #409 | a clean deletion | what the deletion left uncovered (an HTTP 409 conflict) |

In each case the primary flow had a test and the adjacent path the same change created or exposed
did not. Naming the gap is not lesser than closing it, but naming nothing is a failed answer:
"none" only holds if you can point to the test that actually exercises the adjacent path.

## The standard

CONSTRAINT for a major feature, skip for a bugfix: the `Q1-Q7:` line you wrote into the body at
Stage 1 is now checkable against real code. Confirm each answer still holds, or name the one that
stopped being true and what you did about it. Q2 (can a developer who does not need it ignore it
completely) and Q4 (are we absorbing complexity, or transferring it to the user) are the two the
project fails most, so a feature touching the public surface answers those two whatever else it
implicates.

## The outside question

CONSTRAINT for any change to a public symbol: write the smallest call a user makes, as they would
write it, and count what they have to know to write it. A concept in that line the issue did not
introduce is this change transferring complexity, whatever the diff looks like.

Every other question here reads the diff. "Simple on the outside" is not visible from inside it,
which is why this one is asked separately and answered with a call rather than an argument.

## The second reader

CONSTRAINT: whatever a human can see or drive here, a coding agent can reach the same way. A typed
payload, a return value, an event on the log, a CLI read path: any one of them answers this. If the
only way a program learns what happened is parsing terminal output, the capability is half-built and
Q7 fails, which is the argument the binding SPI itself was built on.

## The shape questions

CONSTRAINT: answer all ten, against `git diff dev...HEAD`, honestly.

1. Did this introduce a second way of doing something?
2. Could any new helper reuse existing code?
3. Any class/interface with one trivial caller?
4. Any comment narrating code?
5. Public API grown beyond the budget?
6. Configuration added without a real need?
7. Duplicated validation/error handling?
8. Is every changed file necessary?
9. Could the diff be smaller without obscuring the design?
10. Does the new code look like its canonical neighbors?
