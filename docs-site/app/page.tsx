import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import { getMDXComponents } from '@/mdx-components'
import { source } from '@/lib/source'

// The landing page carries no navbar, sidebar or TOC, which is why it is its own route rather
// than a `full` docs page: `content/_meta.ts` used to say the same thing as a `theme` block.
function landing() {
  const page = source.getPage([])
  if (!page) notFound()
  return page
}

export function generateMetadata(): Metadata {
  const { data } = landing()
  // Next.js skips a `title.template` on the segment that declares it, and `/` is that segment.
  return { title: `${data.title} | AgentDeck SDK`, ...(data.description && { description: data.description }) }
}

export default function Home() {
  const MDX = landing().data.body

  return (
    <main data-pagefind-body>
      <MDX components={getMDXComponents()} />
    </main>
  )
}
