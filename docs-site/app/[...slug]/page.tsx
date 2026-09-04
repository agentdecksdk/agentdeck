import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import { DocsBody, DocsPage } from 'fumadocs-ui/layouts/docs/page'
import { getMDXComponents } from '../../mdx-components'
import { PageFeedback } from '../page-feedback'
import { source } from '../source'

// A *required* catch-all: `/` is the landing page's own route (`app/page.tsx`), which renders
// outside the docs shell. An optional catch-all would claim it and put a sidebar on it.
export async function generateStaticParams() {
  return source.generateParams().filter(({ slug }) => slug.length > 0)
}

export async function generateMetadata(props: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const { slug } = await props.params
  const page = source.getPage(slug)
  if (!page) notFound()
  return { title: page.data.title, description: page.data.description }
}

export default async function Page(props: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await props.params
  const page = source.getPage(slug)
  if (!page) notFound()
  const MDX = page.data.body

  return (
    <DocsPage toc={page.data.toc} tableOfContent={{ footer: <PageFeedback /> }}>
      {/* Pagefind indexes only what carries this attribute, so the index stays page text rather
          than the navigation repeated 44 times. `nextra-theme-docs` emitted it for us. */}
      <DocsBody data-pagefind-body>
        <MDX components={getMDXComponents()} />
      </DocsBody>
    </DocsPage>
  )
}
