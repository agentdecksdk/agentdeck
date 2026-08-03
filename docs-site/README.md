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

## Content

Documentation pages live under `content/`. Navigation is controlled by colocated `_meta.ts` files.
