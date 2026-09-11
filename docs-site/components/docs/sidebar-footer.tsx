'use client'

import type { ComponentProps } from 'react'
import { ThemeSwitch } from '@/components/docs/theme-switch'

/** The theme control, at the foot of the sidebar as v6.0.3 had it.
 *
 *  A *component*, not an element: `layouts/notebook`'s own footer is the mobile icon-link strip,
 *  so the element form lands inside its `hidden … max-lg:flex` div and renders at no width once
 *  `links` is empty. `renderFooter` hands a component the props and steps aside, which makes this
 *  the whole footer rather than something placed inside fumadocs-ui's.
 *
 *  Its own module with `'use client'`, because a function prop cannot cross the RSC boundary into
 *  `DocsLayout`.
 */
export function SidebarFooter(props: ComponentProps<'div'>) {
  return (
    <div {...props} className="flex flex-row items-center border-t px-4 py-2.5">
      <ThemeSwitch />
    </div>
  )
}
