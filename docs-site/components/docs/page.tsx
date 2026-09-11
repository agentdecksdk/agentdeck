'use client'

import { Fragment, useMemo, type ReactNode } from 'react'
import { ChevronLeft, ChevronRight, Text } from 'lucide-react'
import Link from 'fumadocs-core/link'
import { usePathname } from 'fumadocs-core/framework'
import { getBreadcrumbItemsFromPath } from 'fumadocs-core/breadcrumb'
import type { TOCItemType } from 'fumadocs-core/toc'
import type { Item } from 'fumadocs-core/page-tree'
import { TOCProvider, TOCScrollArea, useTOCItems } from 'fumadocs-ui/components/toc'
import { TOCEmpty, TOCItem, TOCItems } from 'fumadocs-ui/components/toc/default'
import { useTreeContext, useTreePath } from 'fumadocs-ui/contexts/tree'
import { useFooterItems } from 'fumadocs-ui/utils/use-footer-items'
import { PageFeedback } from '@/components/docs/page-feedback'

/**
 * The docs article, the breadcrumb above it, the prev/next pair under it and the table of contents
 * beside it. Fumadocs supplies the scroll-spy and the page tree; the arrangement is ours.
 *
 * `--fd-toc-width` is set here rather than on the shell because a custom property inherits down,
 * and `@variant layout` resolves it against the grid ancestor. The shell's `toc` track reads it.
 */
export function DocsArticle({ toc, children }: { toc: TOCItemType[]; children: ReactNode }) {
  return (
    <TOCProvider toc={toc}>
      <main className="grid [grid-area:main]" data-layout-main="">
        <article
          data-layout-content=""
          className="flex flex-col min-w-0 px-4 py-6 gap-4 md:px-6 md:pt-8 xl:px-8 xl:pt-14 *:max-w-[900px]"
        >
          <Breadcrumb />
          {children}
          <PrevNext />
        </article>
      </main>
      <Toc />
    </TOCProvider>
  )
}

function Breadcrumb() {
  const path = useTreePath()
  const { root } = useTreeContext()
  const items = useMemo(() => getBreadcrumbItemsFromPath(root, path, {}), [root, path])
  if (items.length === 0) return null

  return (
    <div className="flex items-center gap-1.5 text-sm text-fd-muted-foreground">
      {items.map((item, i) => {
        const last = i === items.length - 1
        const className = last ? 'truncate text-fd-primary font-medium' : 'truncate'
        return (
          <Fragment key={i}>
            {i !== 0 && <ChevronRight className="size-3.5 shrink-0" />}
            {item.url ? (
              <Link href={item.url} className={`${className} transition-opacity hover:opacity-80`}>
                {item.name}
              </Link>
            ) : (
              <span className={className}>{item.name}</span>
            )}
          </Fragment>
        )
      })}
    </div>
  )
}

// Trailing slashes differ between the tree's urls and the router's pathname on a static export.
const normalize = (url: string) => (url.length > 1 && url.endsWith('/') ? url.slice(0, -1) : url)

function PrevNext() {
  const items = useFooterItems()
  const pathname = usePathname()
  const { previous, next } = useMemo(() => {
    const i = items.findIndex((item) => normalize(item.url) === normalize(pathname))
    return i === -1 ? {} : { previous: items[i - 1], next: items[i + 1] }
  }, [items, pathname])

  return (
    <div className={`@container grid gap-4 ${previous && next ? 'grid-cols-2' : 'grid-cols-1'}`}>
      {previous && <PrevNextItem item={previous} index={0} />}
      {next && <PrevNextItem item={next} index={1} />}
    </div>
  )
}

function PrevNextItem({ item, index }: { item: Item; index: 0 | 1 }) {
  const Icon = index === 0 ? ChevronLeft : ChevronRight
  return (
    <Link
      href={item.url}
      className={`flex flex-col gap-2 rounded-lg border p-4 text-sm transition-colors hover:bg-fd-accent/80 hover:text-fd-accent-foreground @max-lg:col-span-full${index === 1 ? ' text-end' : ''}`}
    >
      <div
        className={`inline-flex items-center gap-1.5 font-medium${index === 1 ? ' flex-row-reverse' : ''}`}
      >
        <Icon className="-mx-1 size-4 shrink-0 rtl:rotate-180" />
        <p>{item.name}</p>
      </div>
      <p className="text-fd-muted-foreground truncate">
        {item.description ?? (index === 0 ? 'Previous Page' : 'Next Page')}
      </p>
    </Link>
  )
}

function Toc() {
  const items = useTOCItems()

  return (
    <div
      className="ad-toc sticky top-(--fd-docs-row-3) [grid-area:toc] h-[calc(var(--fd-docs-height)-var(--fd-docs-row-3))] flex flex-col w-(--fd-toc-width) pt-12 pe-4 pb-2 xl:layout:[--fd-toc-width:268px] max-xl:hidden"
      role="navigation"
      aria-label="Table of contents"
    >
      <h3 id="toc-title" className="inline-flex items-center gap-1.5 text-sm text-fd-muted-foreground">
        <Text className="size-4" />
        On this page
      </h3>
      <TOCScrollArea>
        <TOCItems>
          {items.length === 0 && <TOCEmpty />}
          {items.map((item) => (
            <TOCItem key={item.url} item={item} />
          ))}
        </TOCItems>
      </TOCScrollArea>
      <PageFeedback />
    </div>
  )
}
