import { loader } from 'fumadocs-core/source'
import { docs } from '@/.source/server'

// `baseUrl: '/'`: the docs are the site, so `content/reference/run.mdx` is `/reference/run`.
export const source = loader({ baseUrl: '/', source: docs.toFumadocsSource() })

/** Every page's route slug, e.g. `"build-your-deck/deck"`. The loader owns the content tree, so
 *  this cannot drift from the URLs the way a second walk over `content/` did. */
export function pageSlugs(): string[] {
  return source.getPages().map(page => page.slugs.join('/')).filter(Boolean)
}
