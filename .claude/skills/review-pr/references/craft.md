# Craft phase

Does this read like it belongs in the repo. Judge only after Attack; craft does not matter if the design is wrong.

## Pattern and sibling alignment

- Read the `docs/patterns/` file for this PR's concern before judging it (see `docs/patterns/README.md` for the map). A new way of doing something a pattern already covers is a defect, not a style choice.
- Pick 2-3 canonical siblings from `uv run scripts/repomap.py` (same package, same responsibility class), read them end to end, and compare naming, error handling, dependency usage, structure, typing, and test style.
- Verify the PR's `## Analog` claim against the actual code, not the PR's description of it.

## The Harvest bar

`docs/patterns/` is 106 lines total across four files (`comments.md` 30, `errors.md` 24,
`lifecycle.md` 22, `tests.md` 18, plus a 12-line README): the accumulated taste of the whole
project. That is short enough to read in one sitting, which is the property that makes it worth
reading. A reviewer appending once per PR would double it in a week. The default in Harvest is to
write nothing.

A `docs/patterns/` entry requires all four of these. Any one missing means no entry:

1. **It recurred.** The same good or bad shape appears in at least two places: this PR plus an
   existing occurrence in the codebase, or two PRs. One PR doing something well is an anecdote.
2. **It generalizes.** If the guidance only applies to this module or this adapter, it is an
   inline comment, not a pattern. A pattern tells the next author what to do in code not yet seen.
3. **It is a real good/bad pair from real code**, cited by file and PR. Never invented for the
   illustration.
4. **It is not already covered.** Read all four existing files first. Extending one is strongly
   preferred over creating a fifth.

The same bar applies to a harness note about dev-agent behavior: it needs two cited PRs, not one.

Pattern entries are not scored by volume. The measure is whether a later review cites an entry,
never how many were written. Something that fails any of the four tests is a NIT, or nothing; it
is never a pattern.
- Product philosophy (`docs/engineering/principles.md`): does the change leak internal plumbing (stores, resolvers, internal contexts) into a public API; does it keep "one obvious path"; is it free of speculative abstraction and configuration nothing needs yet.

## Reuse and duplication

- The PR body must carry a `## Reuse analysis` on any PR adding public symbols; its absence is a finding.
- Check every new public class/function against `scripts/repomap.py`: overlap with an existing abstraction's responsibility is a finding that cites the existing symbol.

## Comment and test craft

- Comments: 1-2 lines of non-obvious *why*, per `docs/patterns/comments.md`. Anything restating the code is slop.
- Tests read as a spec: names state the contract under test (`docs/patterns/tests.md`), not the mechanism (`test_it_works`).
- CHANGELOG entry present under `[Unreleased]` for any user-visible change.

## Body craft

The PR body is in scope. It describes the change as it stands, not the rounds that produced it; a body carrying resolved-finding answers or abandoned design is a finding, since the next reader has to wade through it. Count with fenced code stripped, headings and table rows included:

    gh pr view <n> --json body -q .body | perl -0pe 's/```.*?```/ /gs' | wc -w

| section | max |
|---|---|
| opening summary | 40 words |
| `## Reuse analysis` | 80 words |
| `## Analog` | 40 words |
| `## Concept budget` | 4 lines, no prose |
| `## Expected delta` | 1 line |
| `## Design` | 200 words, no subsections |
| whole body | 500 words, or 800 when the diff touches 20+ files, where the extra is a list, not prose |

The total exceeds the sections' sum because a body also carries `Closes #<n>`, a `## Left for the docs-site pass` list, and the docs-impact acknowledgement. File count, not net LOC, decides the large-diff cap: LOC would admit an 8-file PR that simply wrote too much, which is the case these caps exist for.
