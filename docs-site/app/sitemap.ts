import fs from 'node:fs'
import path from 'node:path'
import type { MetadataRoute } from 'next'
import { SITE } from './site'

// `output: 'export'` builds every route ahead of time, and a route handler has to opt in
// explicitly or the build refuses rather than guessing. Nothing here reads a request.
export const dynamic = 'force-static'

/**
 * Every page, read off the content directory rather than listed by hand.
 *
 * A hand-kept sitemap is a second index of the site that nobody remembers to update, and a
 * sitemap missing a page is worse than no sitemap  -  it tells a crawler the page is not part of
 * the site. `lastModified` comes from the file's own mtime, so a page that changed says so.
 */
const CONTENT = path.join(process.cwd(), 'content')

function pages(dir: string, base = ''): { slug: string; mtime: Date }[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return pages(full, base ? `${base}/${entry.name}` : entry.name)
    if (!entry.name.endsWith('.mdx')) return []
    const stem = entry.name.replace(/\.mdx$/, '')
    const slug = stem === 'index' ? base : base ? `${base}/${stem}` : stem
    return [{ slug, mtime: fs.statSync(full).mtime }]
  })
}

export default function sitemap(): MetadataRoute.Sitemap {
  return pages(CONTENT).map(({ slug, mtime }) => ({
    url: slug ? `${SITE}/${slug}` : SITE,
    lastModified: mtime,
    // The entry path and the concept pages are what a stranger should land on; reference pages
    // are for someone already here. Priority is a hint, not a ranking, so this only says which
    // half of the site answers a first question.
    changeFrequency: 'weekly' as const,
    priority: slug === '' ? 1 : slug.startsWith('reference/') ? 0.5 : 0.8
  }))
}
