'use client'

import { usePathname } from 'fumadocs-core/framework'
import Link from 'fumadocs-core/link'
import type { Item } from 'fumadocs-core/page-tree'
import { SidebarItem as BaseSidebarItem, useFolderDepth } from 'fumadocs-ui/components/sidebar/base'
import { cn } from '@/lib/utils'

/** A page in the sidebar, named by the colour of its ink.
 *
 *  fumadocs-ui fills the whole row with `bg-fd-primary/10` for the current page, which makes a
 *  block of the nav where a change of ink is enough. Overriding `sidebar.components.Item` is what
 *  replaces the CSS that used to undo it: `#nd-sidebar a[data-active='true']` reached into a
 *  private DOM id to unpaint something we can simply not paint.
 *
 *  The indent comes from `useFolderDepth`, the same hook the default uses, so a nested page still
 *  lines up under its section.
 */
export function SidebarItem({ item }: { item: Item }) {
  const pathname = usePathname()
  const depth = useFolderDepth()
  const active = pathname === item.url || pathname === `${item.url}/`
  return (
    <BaseSidebarItem
      href={item.url}
      icon={item.icon}
      active={active}
      className={cn(
        'relative flex flex-row items-center gap-2 rounded-lg p-2 text-start text-fd-muted-foreground',
        'wrap-anywhere transition-colors hover:bg-fd-accent/50 hover:text-fd-accent-foreground/80',
        'data-[active=true]:text-fd-primary [&_svg]:size-4 [&_svg]:shrink-0',
        // A page inside a section is marked by a rule in the gutter as well as by its colour;
        // a top-level page has no gutter to put one in.
        depth >= 1 &&
          'data-[active=true]:before:absolute data-[active=true]:before:bg-fd-primary data-[active=true]:before:w-px data-[active=true]:before:inset-y-2.5 data-[active=true]:before:inset-s-2.5'
      )}
      style={{ paddingInlineStart: `calc(${depth * 3 + 2} * var(--spacing))` }}
    >
      {item.name}
    </BaseSidebarItem>
  )
}
