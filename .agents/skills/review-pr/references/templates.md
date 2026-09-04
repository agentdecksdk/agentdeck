# Report templates

Every review output falls into one of three templates. Low freedom: this is the narrow bridge,
not the open field. Structure, length and style are fixed here; do not paraphrase the numbers.

Contents: Template A (inline comment), Template B (verdict), worked examples, Template C
(orchestrator return), style rules, why the numbers are these numbers.

## Template A: inline comment

ALWAYS use this exact template structure:

    **CLASS** Consequence in one sentence, naming the trigger.

    Settled by: <DISCUSS only, one line>

    ```suggestion
    <optional, only when the fix is a literal line replacement>
    ```

- **40 words maximum**, excluding a suggestion block. One paragraph. No headings, no lists, no
  second paragraph.
- The first token is the class in bold. Nothing precedes it.
- `Settled by:` appears on DISCUSS and nowhere else. It names the evidence or decision that would
  resolve it, so a DISCUSS can never be a shrug.

## Template B: verdict

ALWAYS use this exact template structure:

    ## Verdict

    | dimension | result |
    |---|---|
    | Claims | PASS |
    | Attack | 1 BLOCK |
    | Craft | PASS |
    | Scope | PASS |

    ### BLOCK
    - `deck.py:214` consequence in one line

    ### DISCUSS
    - `context.py:88` question. Settled by: what would settle it

    ### DEFER
    - #431 consequence in one line

    <details><summary>NIT (4)</summary>

    - `service.py:12` ...
    </details>

    +214 LOC, 3 public symbols, against a declared 150 and 2

    0 BLOCK, 1 DISCUSS answered, 1 DEFER #431, 4 NIT

- **300 words maximum**, excluding the rubric table, the collapsed NIT block and the delta line.
- The delta line is measured against the PR's `## Expected delta`, one line. It reports; it never
  argues. A body with no estimate reads `+214 LOC, 3 public symbols, no estimate declared`. When it
  triggered the overrun pass (`overrun.md`), the cause that pass named appears in a section above,
  never as commentary on the number.
- **Per finding: 25 words. A DISCUSS gets 35**, because it carries `Settled by:`.
- Sections in exactly this order. **An empty section is omitted entirely**, never written as
  "None".
- NITs are always inside the `<details>` block and never inline on the diff.
- The counts line is last and alone, so the verdict is legible without reading the findings.

## Worked examples

Real v5 findings, situation then the exact comment.

**BLOCK**, from #420: `Deck._mint` compiled a minted agent against `catalog={}` (`deck.py:984`),
so forking any agent declaring `subagents=` raised `NotFoundError` against a catalog that in fact
held the name.

    **BLOCK** `Deck._mint` compiles the minted agent against `catalog={}` (`deck.py:984`), so
    `ctx.agents.fork` on any agent declaring `subagents=` raises `NotFoundError: ... Available:
    []` even though the deck's own catalog holds the name.

Bad version of the same finding, observation instead of consequence, never write this:

    I verified by reproducing the fork call that `Deck._mint` passes `catalog={}`, and confirmed
    through testing that this causes a `NotFoundError` when subagents are declared, traced to
    line 984.

**DISCUSS**, from #415: `can_resume` (`context.py:343`) treats `RESUMABLE_STATUSES` as
`{paused, waiting_answer}`, so a `PAUSED` child is spared too, but both docstrings state only the
narrower "waiting for an answer" rule.

    **DISCUSS** `can_resume` (`context.py:343`) spares a `PAUSED` child too, wider than both
    docstrings' "waiting for an answer" rule.

    Settled by: confirm sparing a paused child is intended and update both docstrings plus a
    test, or narrow the predicate to `WAITING_ANSWER`.

**NIT**, from #420: the PR body's `## Recorded judgments` claimed two rulings landed in the design
doc; only one row did.

    **NIT** Body says both rulings landed in the design doc; only the `fork(source)` row does.
    The pause ruling is missing from `docs/design/execution-api.md`.

## Template C: return to the orchestrator

The only uncapped output. Everything the review currently narrates goes here instead of on the
PR: mutation results, `quality_delta.py` numbers, `concept_budget.py` declared versus actual,
sibling comparison, `make check` result, probe limits. Raw data, no prose.

Fixed order: verdict, `make check` result, counts per class, links to the review and any issues
filed, then the evidence.

**Report a mutation as its result, never as a file you touched.** Mutation edits are local to your
own worktree and restored before you finish; you push nothing, ever. Write "reverting
`deck.py:214` leaves 14 tests green", not "files touched during review". The second phrasing reads
as a reviewer editing the PR, which is the one thing this role forbids, and it costs the reader a
`git log` to disprove.

## Style, applies to all three templates

1. No em dashes anywhere.
2. A finding names a consequence, not an observation: "orphans the child run when a sibling
   raises," not "I verified by mutation that the refusal path...". If no concrete consequence can
   be named, it is a NIT.
3. No first person about method on the PR: no "I ran", "I verified", "I checked". Do the work,
   report the conclusion. The method goes in Template C.
4. Present tense, active voice.
5. Code references as `file.py:line`, in backticks.
6. No praise sections and no closing summary paragraph. Good code is acknowledged in a
   `docs/patterns/` entry during Harvest, with the PR as the citation, not as a comment on the PR.
7. No restating what the diff does. The author wrote it.

## Why the numbers are these numbers

Eight findings across BLOCK, DISCUSS, and DEFER at 25-35 words each is roughly 240 words, plus
headings and the marker line leaves headroom inside 300. A review needing more than eight findings
is a PR that should be blocked and split; phase 4 (Scope) already says so.
