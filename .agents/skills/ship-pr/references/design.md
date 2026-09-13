# Design (Stage 1)

LOW freedom on format: CI (`scripts/concept_budget.py`, the body-length check) parses these
sections literally, so their shape is not yours to vary. HIGH freedom on content: what argument
belongs in `## Reuse analysis` is judgment, same as the design itself.

## PR body sections

ALWAYS use this exact structure, written into the draft PR body before the first source edit:

- Opening summary before the first heading: max 40 words.
- `## Reuse analysis` (max 80 words): existing abstractions considered, the reuse decision, and
  for anything new, why each existing candidate is insufficient.
- `## Analog` (max 40 words): the closest existing analog (module, adapter, test file). Read it
  end to end; name what you will match (shape, naming, error style, test style).
- `## Concept budget` (4 lines, no prose): `new classes: N`, `new public symbols: N`,
  `new modules: N`, `new dependencies: N`.
- `## Expected delta` (1 line): predicted net code LOC.
- `## Design` (max 200 words, no subsections): what the change does. A subsection means it grew
  past what the issue ruled; split the PR instead.
- `Closes #<n>`, a `## Left for the docs-site pass` list, and the docs-impact acknowledgement
  (`make check` names the affected pages; open each and either fix it or list it as reviewed).

CONSTRAINT: whole body 500 words, or 800 when the diff touches 20+ files, where the extra is a
list, never prose. Consequence: over the cap is a BLOCK from the reviewer, counted with:

    gh pr view <n> --json body -q .body | perl -0pe 's/```.*?```/ /gs' | wc -w

Headings and table rows count. A table is instead of paragraphs, not stacked on top of them.

## What a real `## Reuse analysis` and `## Analog` look like

Good, real (#420, `subagents=`): "Both forms are `Deck._invoke`, the #392 seam: no second
execution path, no new event kind, no `agent.changed`. A subagent is a generated entry in
`tools=`, compiled through the catalog-resolver route `resolve_skills` already travels." It names
the existing seam, states what is genuinely new, and stops.

Bad, illustrative: "We considered a few approaches and built a new `AgentMinter` for clarity and
future extensibility." Names no existing code, gives no reason the existing route was
insufficient.

Good, real (#415, `ctx.parallel`): "`agentdeck/core/context.py`'s `Suspender` +
`WorkflowCtx.ask` / `WorkflowCtx._waiting`, and `tests/test_native_workflow.py`. Both read end to
end." Then names, per line, shape/naming/error-style/test-style it will match.

Bad, illustrative: "Similar to other validation elsewhere in the codebase." Names no file, commits
to matching nothing checkable.

## The concept budget is a ceiling, not a score

`concept_budget.py` fails CI only when actuals exceed the declaration; that is the entire check.
Hitting your own number is not an achievement: #420 declared `2/2/0/0` and `+854` and landed both
exactly, which is unexamined precision, not a virtue. #415 declared 4 new public symbols and
shipped 0, with no consequence either way; that gap is fine, because the ceiling's only job is to
stop overrun, never to reward a correct guess. Do not round a real estimate up to look safe, and
do not treat matching it as a design goal.

## The design gate

Before Stage 2, the orchestrator (`ship-issue` step 2) compares this design against the brief it
formed at its own step 0. CONSTRAINT: stop after posting the design and wait; do not edit source
until told to proceed. Size alone is never the finding that sends a design back: only a named
problem is, e.g. a duplication of something already in `scripts/repomap.py`'s output, an
abstraction with one caller, a divergence from your own declared `## Analog`, configuration for a
value that never changes. Sent back: revise Stage 1 against the named problem, not toward a
smaller line count, and post again.
