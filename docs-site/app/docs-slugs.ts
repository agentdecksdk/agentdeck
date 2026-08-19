// Server-only: reads the content tree at build time. Never import this from a 'use client'
// module -- `fs` has no browser build.
import { readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

/** Every real page's slug (e.g. "build-your-deck/deck"), read from the .mdx files on disk so the
 * list can never rot out of sync with the site. */
export function docsSlugs(root: string = join(process.cwd(), 'content')): string[] {
  const slugs: string[] = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full)
      else if (entry.endsWith('.mdx')) slugs.push(relative(root, full).replace(/\.mdx$/, '').replaceAll('\\', '/'))
    }
  }
  walk(root)
  return slugs
}
