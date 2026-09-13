import { defineDocs } from 'fumadocs-mdx/config'

// `content/`, not Fumadocs' `content/docs/` default: `scripts/check_docs_impact.py` keys the
// docs-impact gate on `docs-site/content/**.mdx`, and every published URL is `content/`-relative.
export const docs = defineDocs({ dir: 'content' })
