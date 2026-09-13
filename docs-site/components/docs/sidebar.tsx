'use client'

import type { ComponentProps, CSSProperties } from 'react'
import { createPageTreeRenderer } from 'fumadocs-ui/components/sidebar/page-tree'
import {
  SidebarContent as BaseContent,
  SidebarDrawerContent,
  SidebarFolder,
  SidebarFolderContent as BaseFolderContent,
  SidebarFolderLink as BaseFolderLink,
  SidebarFolderTrigger as BaseFolderTrigger,
  SidebarItem as BaseItem,
  SidebarSeparator as BaseSeparator,
  SidebarViewport,
  useFolder,
  useFolderDepth,
  useSidebar
} from 'fumadocs-ui/components/sidebar/base'
import { SidebarFooter } from '@/components/docs/sidebar-footer'
import { SidebarItem } from '@/components/docs/sidebar-item'
import { useSheet } from '@/lib/sheet'
import { cn } from '@/lib/utils'

// Every row is indented by its depth, so a nested page lines up under the section that holds it.
const offset = (depth: number): CSSProperties => ({
  paddingInlineStart: `calc(${2 + 3 * depth} * var(--spacing))`
})

const ROW =
  'relative flex flex-row items-center gap-2 rounded-lg p-2 text-start text-fd-muted-foreground' +
  ' wrap-anywhere [&_svg]:size-4 [&_svg]:shrink-0' +
  ' transition-colors hover:bg-fd-accent/50 hover:text-fd-accent-foreground/80 hover:transition-none'

function Separator({ className, style, ...props }: ComponentProps<typeof BaseSeparator>) {
  const depth = useFolderDepth()
  return (
    <BaseSeparator
      className={cn(
        'inline-flex items-center gap-2 mb-1.5 px-2 mt-6 empty:mb-0 [&_svg]:size-4 [&_svg]:shrink-0',
        depth === 0 && 'first:mt-0',
        className
      )}
      style={{ ...offset(depth), ...style }}
      {...props}
    />
  )
}

function FolderTrigger({ className, style, ...props }: ComponentProps<typeof BaseFolderTrigger>) {
  const depth = useFolder()?.depth ?? 0
  return (
    <BaseFolderTrigger
      className={cn(ROW, 'w-full', className)}
      style={{ ...offset(depth - 1), ...style }}
      {...props}
    />
  )
}

function FolderLink({ className, style, ...props }: ComponentProps<typeof BaseFolderLink>) {
  const depth = useFolderDepth()
  return (
    <BaseFolderLink
      className={cn(
        ROW,
        'data-[active=true]:bg-fd-primary/10 data-[active=true]:text-fd-primary data-[active=true]:hover:transition-colors',
        depth > 1 &&
          "data-[active=true]:before:content-[''] data-[active=true]:before:bg-fd-primary data-[active=true]:before:absolute data-[active=true]:before:w-px data-[active=true]:before:inset-y-2.5 data-[active=true]:before:inset-s-2.5",
        'w-full',
        className
      )}
      style={{ ...offset(depth - 1), ...style }}
      {...props}
    />
  )
}

function FolderContent({ className, children, ...props }: ComponentProps<typeof BaseFolderContent>) {
  const depth = useFolderDepth()
  return (
    <BaseFolderContent
      className={cn(
        'relative',
        depth === 1 &&
          "before:content-[''] before:absolute before:w-px before:inset-y-1 before:bg-fd-border before:inset-s-2.5",
        className
      )}
      {...props}
    >
      <div className="flex flex-col gap-0.5 pt-0.5">{children}</div>
    </BaseFolderContent>
  )
}

// The renderer needs a default page row, but `Item` below always overrides it. Styled the same
// way regardless, so the two can never drift into looking like different navigations.
function DefaultItem({ className, style, ...props }: ComponentProps<typeof BaseItem>) {
  const depth = useFolderDepth()
  return (
    <BaseItem
      className={cn(
        ROW,
        'data-[active=true]:bg-fd-primary/10 data-[active=true]:text-fd-primary data-[active=true]:hover:transition-colors',
        depth >= 1 &&
          "data-[active=true]:before:content-[''] data-[active=true]:before:bg-fd-primary data-[active=true]:before:absolute data-[active=true]:before:w-px data-[active=true]:before:inset-y-2.5 data-[active=true]:before:inset-s-2.5",
        className
      )}
      style={{ ...offset(depth), ...style }}
      {...props}
    />
  )
}

const PageTree = createPageTreeRenderer({
  SidebarFolder,
  SidebarFolderContent: FolderContent,
  SidebarFolderLink: FolderLink,
  SidebarFolderTrigger: FolderTrigger,
  SidebarItem: DefaultItem,
  SidebarSeparator: Separator
})

const NAV = { role: 'navigation', 'aria-label': 'Sidebar' } as const

/** The docs sidebar: a rail beside the article, a sheet under the bar below the docs breakpoint.
 *
 *  Both render the same page tree. `nav.mode` was always `'top'` here and `collapsible` always
 *  false, so the branches `layouts/notebook`'s own sidebar carried for the other two arrangements
 *  (a nav title in the rail, a collapse trigger, a tabs dropdown, an icon-link strip) are gone
 *  rather than reproduced: nothing in this site ever reached them.
 */
export function DocsSidebar() {
  const { open, mode } = useSidebar()
  // One element for both arrangements, and no scroll plumbing on it: `SidebarViewport` is already
  // `flex-1 min-h-0` over a `size-full` scroller, which resolves and scrolls the moment its
  // ancestor has a definite height. The rail gets that from the grid row, the sheet from being
  // pinned top and bottom. Nothing here has to know which one it is in.
  const viewport = (
    <SidebarViewport>
      <PageTree Item={SidebarItem} />
    </SidebarViewport>
  )

  useSheet(mode === 'drawer' && open)

  return (
    <>
      <BaseContent>
        {({ ref, collapsed, hovered, ...rest }) => (
          <div
            data-sidebar-placeholder=""
            className="sticky z-20 [grid-area:sidebar] pointer-events-none *:pointer-events-auto md:layout:[--fd-sidebar-width:268px] max-md:hidden top-(--fd-docs-row-2) h-[calc(var(--fd-docs-height)-var(--fd-docs-row-2))]"
          >
            {/* `collapsed` and `hovered` are render-prop state, not DOM attributes: React warns
                on a boolean it does not know, so they go on as data attributes the way
                `layouts/notebook` put them. */}
            <aside
              ref={ref}
              data-collapsed={collapsed}
              data-hovered={hovered}
              className="absolute flex flex-col w-full inset-s-0 inset-y-0 items-end text-sm duration-250 *:w-(--fd-sidebar-width)"
              {...NAV}
              {...rest}
            >
              <div className="flex flex-col gap-3 p-4 pb-2 empty:hidden" />
              {viewport}
              <SidebarFooter />
            </aside>
          </div>
        )}
      </BaseContent>

      {/* No overlay below the breakpoint: the sheet fills the screen under the bar, so there is
          nothing behind it to dim and nothing outside it to click. */}
      <SidebarDrawerContent
        className="ad-sheet text-[0.9375rem] bg-fd-background [--ad-sheet-top:var(--fd-docs-row-2)]"
        {...NAV}
      >
        {viewport}
        <SidebarFooter className="ad-sheet__foot" />
      </SidebarDrawerContent>
    </>
  )
}
