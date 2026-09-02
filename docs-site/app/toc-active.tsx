'use client'

import { useEffect } from 'react'

/**
 * Marks the section you are reading in the table of contents.
 *
 * Nextra's TOC renders no active state and exposes no hook for one, so this observes the headings
 * it links to and sets a class on the matching link. Reaching into the theme's DOM is the cost of
 * that: the alternative is replacing the whole TOC to add one class.
 */
export function TocActive() {
  useEffect(() => {
    const links = new Map<string, HTMLAnchorElement>()
    for (const link of document.querySelectorAll<HTMLAnchorElement>('.nextra-toc a[href^="#"]')) {
      links.set(decodeURIComponent(link.hash.slice(1)), link)
    }
    if (links.size === 0) return

    const seen = new Set<string>()
    const mark = () => {
      // The topmost heading currently in view, in document order, so scrolling back up moves the
      // mark back up rather than leaving it on the furthest section reached.
      let active: string | null = null
      for (const id of links.keys()) {
        if (seen.has(id)) {
          active = id
          break
        }
      }
      for (const [id, link] of links) link.classList.toggle('is-reading', id === active)
    }

    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (entry.isIntersecting) seen.add(entry.target.id)
          else seen.delete(entry.target.id)
        }
        mark()
      },
      { rootMargin: '-80px 0px -70% 0px' }
    )
    for (const id of links.keys()) {
      const heading = document.getElementById(id)
      if (heading) observer.observe(heading)
    }
    return () => observer.disconnect()
  }, [])

  return null
}
