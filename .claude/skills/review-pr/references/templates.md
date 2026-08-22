# Report templates

Every review output falls into one of three templates. Structure, length, and style are fixed
here; do not paraphrase the numbers.

## Template A: inline comment

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

    <!-- agentdeck-review: pass --> (0 BLOCK, 1 DISCUSS answered, 1 DEFER #431, 4 NIT)

- **300 words maximum**, excluding the rubric table and the collapsed NIT block.
- **Per finding: 25 words. A DISCUSS gets 35**, because it carries `Settled by:`.
- Sections in exactly this order. **An empty section is omitted entirely**, never written as
  "None".
- NITs are always inside the `<details>` block and never inline on the diff.
- The marker line is last, alone, and exactly as specified (`<!-- agentdeck-review: pass -->` or
  `block`, with the counts). A scorecard greps it.

## Template C: return to the orchestrator

The only uncapped output. Everything the review currently narrates goes here instead of on the
PR: mutation results, `quality_delta.py` numbers, `concept_budget.py` declared versus actual,
sibling comparison, `make check` result, probe limits. Raw data, no prose.

Fixed order: verdict, `make check` result, counts per class, links to the review and any issues
filed, then the evidence.

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
