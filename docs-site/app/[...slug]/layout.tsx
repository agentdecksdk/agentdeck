import type { ReactNode } from 'react'
import { DocsLayout } from 'fumadocs-ui/layouts/notebook'
import { DocsShell } from '@/components/docs/shell'
import { SidebarFooter } from '@/components/docs/sidebar-footer'
import { SidebarItem } from '@/components/docs/sidebar-item'
import { SiteHeader } from '@/components/site/header'
import { source } from '@/lib/source'

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={source.getPageTree()}
      slots={{
        container: DocsShell,
        header: SiteHeader
      }}
      /* The bar is ours, so the mark, the version and the search live in it directly rather than
         arriving through `nav`. `themeSwitch` off: v6.0.3 put that control at the foot of the
         sidebar, which is where `sidebar.footer` puts it. */
      themeSwitch={{ enabled: false }}
      /* `collapsible` is what puts the collapse trigger in the bar; v6.0.3 had no such control
         there, and the sidebar is the page's spine rather than something to fold away. */
      sidebar={{
        footer: SidebarFooter,
        collapsible: false,
        components: { Item: SidebarItem },
        role: 'navigation',
        'aria-label': 'Sidebar'
      }}
      nav={{ mode: 'top' }}
    >
      {children}
    </DocsLayout>
  )
}
