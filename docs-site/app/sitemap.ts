import fs from 'node:fs'
import path from 'node:path'
import type { MetadataRoute } from 'next'
import { SITE } from '@/lib/site'
import { source } from '@/lib/source'

// `output: 'export'` builds every route ahead of time, and a route handler has to opt in
// explicitly or the build refuses rather than guessing. Nothing here reads a request.
export const dynamic = 'force-static'

/**
 * Every page, from the loader that owns the routes rather than listed by hand.
 *
 * A hand-kept sitemap is a second index of the site that nobody remembers to update, and a
 * sitemap missing a page is worse than no sitemap  -  it tells a crawler the page is not part of
 * the site. Reading the page tree rather than walking `content/` again is what makes the two
 * agree by construction. `lastModified` still comes from the file's own mtime, which the tree
 * does not carry, so a page that changed says so.
 */
const CONTENT = path.join(process.cwd(), 'content')

export default function sitemap(): MetadataRoute.Sitemap {
  return source.getPages().map(page => {
    const slug = page.slugs.join('/')
    return {
      url: slug ? `${SITE}/${slug}` : SITE,
      lastModified: fs.statSync(path.join(CONTENT, page.path)).mtime,
      // The entry path and the concept pages are what a stranger should land on; reference pages
      // are for someone already here. Priority is a hint, not a ranking, so this only says which
      // half of the site answers a first question.
      changeFrequency: 'weekly' as const,
      priority: slug === '' ? 1 : slug.startsWith('reference/') ? 0.5 : 0.8
    }
  })
}
