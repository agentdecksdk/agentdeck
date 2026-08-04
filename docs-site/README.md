# AgentDeck docs site

MDX-powered documentation built with Nextra 4 and the Next.js App Router.

## Local development

```bash
cd docs-site
npm ci
npm run dev
```

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
`tests/test_docs_site.py` (part of `make check`), which also checks that every internal
link resolves to a page. A block that cannot be checked opts out with a reason:

````text
```python no-test reason="illustrative fragment"
````

Plan and phases: `docs/delivery/docs-site-plan.md`.
