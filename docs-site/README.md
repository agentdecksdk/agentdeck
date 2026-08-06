# AgentDeck docs site

MDX-powered documentation built with Nextra 4 and the Next.js App Router.

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

`package-lock.json` is committed and CI installs with `npm ci`. `zod` is pinned to
`4.3.5` via `overrides`: from zod 4.4.0 a required object key that receives
`undefined` is a hard error, and `nextra-theme-docs@4.6.1`'s `<Layout>` strips
`children` off its props before validating them against a schema that still
requires `children` — every page fails to prerender. Drop the override once
Nextra ships a fix.

## Search

Nextra's search box loads `_pagefind/pagefind.js` at runtime, which `next build` does not
produce — the `postbuild` script indexes `out/` with Pagefind and CI asserts the index
exists. Without it the box renders and finds nothing.

## Content

Documentation pages live under `content/`. Navigation is controlled by colocated `_meta.ts` files.

Python blocks are parsed and their `agentdeck` imports resolved by
`tests/test_docs_site.py` (part of `make check`), which also checks that every **absolute
markdown** link resolves to a page and that `_meta.ts` keys match the top-level pages.
Relative hrefs, reference-style links, MDX `<Cards>`, anchors, and non-Python blocks are
not covered — see the plan's §6 for the full list of ceilings. A block that cannot be
checked opts out with a reason:

````text
```python no-test reason="illustrative fragment"
````

`content/reference/settings.mdx` and `content/reference/cli.mdx` are generated, not
hand-written — `scripts/generate_docs_reference.py` renders them from
`agentdeck/runtime/settings.py`'s `LayeredSettings` subclasses and `agentdeck/cli.py`'s
argparse tree. `tests/test_generated_reference.py` (part of `make check`) regenerates both
in memory and fails if they differ from the committed pages; run `make docs-reference` to
refresh them after a settings or CLI change.

Plan and phases: `docs/delivery/docs-site-plan.md`.
