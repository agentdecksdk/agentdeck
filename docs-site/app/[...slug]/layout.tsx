import type { ReactNode } from 'react'
import { DocsFrame } from '@/components/docs/frame'
import { source } from '@/lib/source'

export default function Layout({ children }: { children: ReactNode }) {
  return <DocsFrame tree={source.getPageTree()}>{children}</DocsFrame>
}
