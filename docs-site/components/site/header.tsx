'use client'

import type { ComponentProps } from 'react'
import { SearchTrigger } from 'fumadocs-ui/layouts/shared/slots/search-trigger'
import { useSearchContext } from 'fumadocs-ui/contexts/search'
import { SidebarTrigger } from 'fumadocs-ui/components/sidebar/base'
import { PanelLeft, Search } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { JackPanel } from '@/components/jack/panel'
import { Mark } from '@/components/site/mark'
import { GitHubMark } from '@/components/site/brand-marks'
import { cn } from '@/lib/utils'
import { CURRENT_VERSION } from '@/lib/version'
import { useDocSlugs } from '@/components/docs/use-doc-slugs'

/**
 * fumadocs' `FullSearchTrigger` hardcodes its shortcut chip: the first key renders "⌘" during SSR
 * and swaps to "Ctrl" after mount on Windows/Linux, with no slot to reserve space around that
 * swap. Rebuilt here, off the same public `useSearchContext`, so the first `kbd` can carry a
 * fixed width: the swap changes the glyph, not the box.
 */
function DesktopSearchTrigger({ className }: { className?: string }) {
  const { enabled, hotKey, setOpenSearch } = useSearchContext()
  if (!enabled) return null
  return (
    <button
      type="button"
      data-search-full=""
      aria-label="Open Search"
      onClick={() => setOpenSearch(true)}
      className={cn(
        'inline-flex items-center gap-2 rounded-lg border bg-fd-secondary/50 p-1.5 ps-2 text-sm text-fd-muted-foreground transition-colors hover:bg-fd-accent hover:text-fd-accent-foreground',
        className
      )}
    >
      <Search className="size-4" />
      Search
      <div className="ms-auto inline-flex gap-0.5">
        {hotKey.map((key, index) => (
          <kbd
            key={index}
            className={cn('rounded-md border bg-fd-background px-1.5', index === 0 && 'inline-block min-w-12 text-center')}
          >
            {key.display}
          </kbd>
        ))}
      </div>
    </button>
  )
}

/** The top bar: the mark, then search, then the assistant and the repository.
 *
 *  Three regions in the order they are read. As `nav.children` the assistant landed beside the
 *  mark, because fumadocs-ui renders those in the bar's *first* region and no prop reaches the
 *  last one; the workaround was `position: absolute` plus a hardcoded 153px reserve on the row so
 *  the search would not grow underneath. Owning the header is what deletes that.
 *
 *  `layout:[--fd-header-height:…]` is how the row height reaches the grid, so it stays: the
 *  container reads it to offset the sidebar and the table of contents.
 */
export function SiteHeader({ className, ...props }: ComponentProps<'header'>) {
  const slugs = useDocSlugs()
  return (
    <header
      {...props}
      className={cn(
        'ad-bar sticky [grid-area:header] top-(--fd-docs-row-1) z-10 flex h-14 items-center gap-2',
        'border-b px-4 backdrop-blur-sm md:px-6 layout:[--fd-header-height:--spacing(14)]',
        className
      )}
    >
      <a href="/" className="inline-flex min-h-11 items-center gap-2.5 font-semibold">
        <Mark size={26} />
        <strong className="ad-wordmark">AgentDeck</strong>
        <Badge variant="secondary" className="docs-version">v{CURRENT_VERSION}</Badge>
      </a>

      <DesktopSearchTrigger className="my-auto ms-auto w-full max-w-sm rounded-xl ps-2.5 max-md:hidden" />

      <div className="ms-auto flex items-center gap-1.5 md:ms-0">
        <JackPanel validSlugs={slugs} />
        {/* Explicit min-width/min-height, not `w-11 h-11`: header.css fixes width/height at 30px
            and only a conflicting min- constraint is guaranteed to win over it regardless of
            stylesheet order. */}
        <a
          className="nav-actions__repo"
          href="https://github.com/agentdecksdk/agentdeck"
          aria-label="GitHub"
        >
          <GitHubMark />
        </a>
        <SearchTrigger hideIfDisabled className="p-[13px] md:hidden" />
        <Button variant="ghost" size="icon" className="size-11 md:hidden" asChild>
          <SidebarTrigger aria-label="Open sidebar"><PanelLeft /></SidebarTrigger>
        </Button>
      </div>
    </header>
  )
}
