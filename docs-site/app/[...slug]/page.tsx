import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import { DocsBody, DocsPage } from 'fumadocs-ui/layouts/notebook/page'
import { getMDXComponents } from '@/mdx-components'
import { PageFeedback } from '@/components/docs/page-feedback'
import { source } from '@/lib/source'

// A *required* catch-all: `/` is the landing page's own route (`app/page.tsx`), which renders
// outside the docs shell. An optional catch-all would claim it and put a sidebar on it.
export async function generateStaticParams() {
  return source.generateParams().filter(({ slug }) => slug.length > 0)
}

export async function generateMetadata(props: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const { slug } = await props.params
  const page = source.getPage(slug)
  if (!page) notFound()
  // Spread, never `description: undefined`: Next reads a present key as an override, and the
  // site description in `app/layout.tsx` would stop cascading to the 14 pages that carry none.
  return { title: page.data.title, ...(page.data.description && { description: page.data.description }) }
}

export default async function Page(props: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await props.params
  const page = source.getPage(slug)
  if (!page) notFound()
  const MDX = page.data.body

  return (
    <DocsPage
      toc={page.data.toc}
      tableOfContent={{
        footer: <PageFeedback />,
        container: { className: 'ad-toc', role: 'navigation', 'aria-label': 'Table of contents' }
      }}
      /* Below the TOC's breakpoint fumadocs-ui puts a sticky bar above the article whose only
         content is a button repeating the page title. The title is already the first thing in
         the article. */
      tableOfContentPopover={{ enabled: false }}
    >
      {/* Pagefind indexes only what carries this attribute, so the index stays page text rather
          than the navigation repeated 44 times. `nextra-theme-docs` emitted it for us. */}
      <DocsBody data-pagefind-body>
        <MDX components={getMDXComponents()} />
      </DocsBody>
    </DocsPage>
  )
}
