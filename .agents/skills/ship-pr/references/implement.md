# Implement (Stage 2)

HIGH freedom. This is an open field, not a checklist: the issue this skill ships from puts
Stage 2's own freedom out of scope, deliberately. Do not turn the rules below into more process
than they are; they bound the edges, not the path through them.

## Match the analog

JUDGMENT: read the `## Analog` you named in Stage 1 end to end, then match its shape, naming,
error style, and test style. Deviating is fine when the analog's own reasoning does not hold for
this case; state why in the PR body's `## Design` when you do.

## Stay in budget

CONSTRAINT: the concept budget declared in Stage 1 is a ceiling. Exceeding it fails CI. No
speculative abstractions, no unrequested configuration surface.

## Bug fixes

JUDGMENT, default: failing regression test first, minimal fix, test passes. This is the default
shape, not a rule with no exceptions; deviate when the issue's own scope calls for it.

## Tests

- CONSTRAINT: tests assert real behavior and invariants without live model calls
  (`agentdeck.testing` scripted models); stub only at the engine SDK boundary. Consequence: a test
  that calls a live model is flaky in CI, and the reviewer's mutation probe cannot trust it.
- CONSTRAINT: `timeout=` on every subprocess call in a test. Consequence: an untimed subprocess
  can hang the gate indefinitely instead of failing.
- EXPECTATION: the reviewer's Attack phase mutation-tests anything a test claims to cover, by
  reverting the fix and confirming the test dies on the expected line. Before the gate, name what
  path this change creates, exposes, or leaves behind that no test exercises, or write the test:
  test what the change exposes, not only what it built.

## Slop and changelog

- EXPECTATION: hooks block slop at write time (SLOP001-009). Fix the finding; never suppress
  without a coded reason.
- CONSTRAINT: update `CHANGELOG.md` under `[Unreleased]` for any user-visible change. Consequence:
  the reviewer's Craft phase files a missing entry as a finding.
- JUDGMENT: push as you go rather than batching commits; nothing in this stage forces a schedule
  beyond that default.
