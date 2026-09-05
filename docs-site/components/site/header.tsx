'use client'

import type { ComponentProps } from 'react'
import { FullSearchTrigger, SearchTrigger } from 'fumadocs-ui/layouts/shared/slots/search-trigger'
import { SidebarTrigger } from 'fumadocs-ui/components/sidebar/base'
import { PanelLeft } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { JackPanel } from '@/components/jack/panel'
import { Mark } from '@/components/site/mark'
import { GitHubMark } from '@/components/site/brand-marks'
import { cn } from '@/lib/utils'
import { CURRENT_VERSION } from '@/lib/version'
import { useDocSlugs } from '@/components/docs/use-doc-slugs'

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
      <a href="/" className="inline-flex items-center gap-2.5 font-semibold">
        <Mark size={26} />
        <strong className="ad-wordmark">AgentDeck</strong>
        <Badge variant="secondary" className="docs-version">v{CURRENT_VERSION}</Badge>
      </a>

      <FullSearchTrigger hideIfDisabled className="my-auto ms-auto w-full max-w-sm rounded-xl ps-2.5 max-md:hidden" />

      <div className="ms-auto flex items-center gap-1.5 md:ms-0">
        <JackPanel validSlugs={slugs} />
        <a className="nav-actions__repo" href="https://github.com/agentdecksdk/agentdeck" aria-label="GitHub">
          <GitHubMark />
        </a>
        <SearchTrigger hideIfDisabled className="p-2 md:hidden" />
        <Button variant="ghost" size="icon" className="md:hidden" asChild>
          <SidebarTrigger aria-label="Open sidebar"><PanelLeft /></SidebarTrigger>
        </Button>
      </div>
    </header>
  )
}
