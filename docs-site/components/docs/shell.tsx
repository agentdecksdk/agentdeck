'use client'

import type { ComponentProps, CSSProperties } from 'react'
import { useEffect, useRef } from 'react'
import { useSidebar } from 'fumadocs-ui/components/sidebar/base'
import { cn } from '@/lib/utils'
import { SiteFooter } from '@/components/site/footer'

const PAGE_COL = 'calc(var(--fd-layout-width, 97rem) - var(--fd-sidebar-col) - var(--fd-toc-width))'
const COLUMNS = `minmax(min-content, 1fr) var(--fd-sidebar-col) minmax(0, ${PAGE_COL}) var(--fd-toc-width) minmax(min-content, 1fr)`

/** The docs grid: a header row, then sidebar, article and table of contents.
 *
 *  `--fd-sidebar-col` is the sidebar's own width rather than a collapsed/expanded switch, because
 *  the layout is mounted with `collapsible: false`; there is no track to animate and no
 *  `data-column-changed` to drive it.
 *
 *  Named areas rather than positions: `slots.header`, the sidebar and the TOC each place
 *  themselves with `[grid-area:…]`, so this file owns the tracks and nothing else.
 *
 *  The sidebar column runs down through the footer row on purpose. A sticky element is bounded
 *  by its own grid area, so with the footer outside the grid the sidebar was pushed up by the
 *  footer's height at full scroll and slid under the bar.
 *
 *  The footer stops at the table-of-contents column and does not reach the trailing gutter. That
 *  gutter is `minmax(min-content, 1fr)`, and a spanning item puts its whole min-content on the one
 *  intrinsically sized track it touches: the footer's own minimum took 133px of a 390px viewport
 *  away from the article.
 */
export function DocsShell({ className, style, children, ...props }: ComponentProps<'div'>) {
  const { open, setOpen, mode } = useSidebar()
  const opener = useRef<HTMLElement | null>(null)

  // The drawer has no native <dialog>, so nothing restores focus to whatever opened it; capture
  // that element on open and hand focus back on close, the way <dialog> would on its own.
  useEffect(() => {
    if (mode !== 'drawer') return
    if (open) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      return
    }
    opener.current?.focus()
    opener.current = null
  }, [open, mode])

  useEffect(() => {
    if (mode !== 'drawer' || !open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [mode, open, setOpen])

  return (
    <div
      {...props}
      style={{
        gridTemplate:
          `". header header header ."\n` +
          `"sidebar sidebar toc-popover toc-popover ."\n` +
          `"sidebar sidebar main toc ." 1fr\n` +
          `"sidebar sidebar footer footer ." auto / ${COLUMNS}`,
        '--fd-docs-row-1': 'var(--fd-banner-height, 0px)',
        '--fd-docs-row-2': 'calc(var(--fd-docs-row-1) + var(--fd-header-height))',
        '--fd-docs-row-3': 'calc(var(--fd-docs-row-2) + var(--fd-toc-popover-height))',
        '--fd-sidebar-col': 'var(--fd-sidebar-width)',
        ...style
      } as CSSProperties}
      className={cn(
        'ad-shell grid overflow-x-clip min-h-(--fd-docs-height) auto-cols-auto auto-rows-auto',
        '[--fd-docs-height:100dvh] [--fd-header-height:0px] [--fd-toc-popover-height:0px]',
        '[--fd-sidebar-width:0px] [--fd-toc-width:0px]',
        className
      )}
    >
      {children}
      <SiteFooter className="[grid-area:footer]" />
    </div>
  )
}
