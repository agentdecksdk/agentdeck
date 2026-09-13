'use client'

import type { ReactNode } from 'react'
import type { Root } from 'fumadocs-core/page-tree'
import { TreeContextProvider } from 'fumadocs-ui/contexts/tree'
import { SidebarProvider } from 'fumadocs-ui/components/sidebar/base'
import { DocsShell } from '@/components/docs/shell'
import { DocsSidebar } from '@/components/docs/sidebar'
import { SiteHeader } from '@/components/site/header'

/** What `DocsLayout` used to assemble: the page tree in context, the sidebar's open state around
 *  it, and the grid that places the bar, the rail and the article.
 *
 *  `DocsLayout` also carried a layout context of its own that only its own slots read. With every
 *  slot ours, nothing reads it, so the three providers here are the whole of it.
 */
export function DocsFrame({ tree, children }: { tree: Root; children: ReactNode }) {
  return (
    <TreeContextProvider tree={tree}>
      <SidebarProvider>
        <DocsShell>
          <SiteHeader />
          <DocsSidebar />
          {children}
        </DocsShell>
      </SidebarProvider>
    </TreeContextProvider>
  )
}
