# AgentDeck docs site

MDX-powered documentation built with Fumadocs and the Next.js App Router.

## Local development

```bash
npm --prefix docs-site ci
npm --prefix docs-site run dev        # http://localhost:3030, hot reload, no search
npm --prefix docs-site run preview    # http://localhost:3031, real build, search works
```

Ports are pinned so they don't drift when something else holds 3000. `dev` has no search:
Pagefind indexes built HTML, so only `preview` (which goes through `npm run build`, firing
`postbuild`) serves an index.

## Production build

```bash
npm run build
```

The static export is generated in `docs-site/out/`.

## Dependencies

`package-lock.json` is committed and CI installs with `npm ci`. `postinstall` runs
`fumadocs-mdx`, which writes the `.source/` entry files the routes import; it is
generated, not committed.

## Search

`app/search-dialog.tsx` loads `_pagefind/pagefind.js` at runtime, which `next build` does
not produce  -  the `postbuild` script indexes `out/` with Pagefind and CI asserts the index
exists. Without it the box renders and finds nothing. Fumadocs' own search would need Orama
and ship the index to the browser, which `docs/delivery/docs-site-plan.md` DS-D8 rules out.

## Content

Documentation pages live under `content/`. Navigation is controlled by colocated `meta.json`
files; a page's sidebar label is its frontmatter `title`, which Fumadocs requires.

Python blocks are parsed and their `agentdeck` imports resolved by
`tests/test_docs_site.py` (part of `make check`), which also checks that every **absolute
markdown** link resolves to a page and that `meta.json` pages match the files beside it.
Relative hrefs, reference-style links, MDX `<Cards>`, anchors, and non-Python blocks are
not covered  -  see the plan's §6 for the full list of ceilings. A block that cannot be
checked opts out with a reason:

````text
```python no-test reason="illustrative fragment"
````

`content/reference/settings.mdx` and `content/reference/cli.mdx` are generated, not
hand-written  -  `scripts/generate_docs_reference.py` renders them from
`agentdeck/runtime/settings.py`'s `LayeredSettings` subclasses and `agentdeck/cli.py`'s
argparse tree. `tests/test_generated_reference.py` (part of `make check`) regenerates both
in memory and fails if they differ from the committed pages; run `make docs-reference` to
refresh them after a settings or CLI change.

Each page's `docs_sources` metadata block maps it to source paths that can change its claims:

```mdx
{/* docs_sources:
  - "agentdeck/deck.py"
  - "agentdeck/runtime/discovery.py"
*/}
```

Pull requests must update each affected page or check the review acknowledgement item after
reviewing the unchanged pages reported by CI. New pages fail the check until they declare this
metadata. Run `make docs-impact` to perform the same check against `origin/dev`.

Plan and phases: `docs/delivery/docs-site-plan.md`.
