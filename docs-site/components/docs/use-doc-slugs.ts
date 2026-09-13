'use client'

import { useMemo } from 'react'
import { useTreeContext } from 'fumadocs-ui/contexts/tree'
import type { Node } from 'fumadocs-core/page-tree'

/** Every documented route, read from the page tree the shell is already inside.
 *
 *  `pageSlugs()` gives the same list from `source`, but that is server data and the bar is a
 *  client component: a slot has to be a client reference to cross the boundary, so it cannot be
 *  handed the array as a prop through a closure. The tree carries the same URLs. */
export function useDocSlugs(): string[] {
  const { root } = useTreeContext()
  return useMemo(() => {
    const out: string[] = []
    const walk = (nodes: Node[]) => {
      for (const node of nodes) {
        if (node.type === 'page') out.push(node.url.replace(/^\/|\/$/g, ''))
        else if (node.type === 'folder') walk(node.children)
      }
    }
    walk(root.children)
    return out.filter(Boolean)
  }, [root])
}
