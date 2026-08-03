import type { Metadata } from 'next'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
import { getPageMap } from 'nextra/page-map'
import type { ReactNode } from 'react'
import 'nextra-theme-docs/style.css'

export const metadata: Metadata = {
  title: {
    default: 'AgentDeck',
    template: '%s | AgentDeck'
  },
  description: 'Build agents, deterministic skills, and durable workflows.'
}

const navbar = (
  <Navbar
    logo={<strong>AgentDeck</strong>}
    projectLink="https://github.com/sagi5060/agentdeck"
  />
)

const footer = (
  <Footer>
    AgentDeck documentation · {new Date().getFullYear()}
  </Footer>
)

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" dir="ltr" suppressHydrationWarning>
      <Head faviconGlyph="A" />
      <body>
        <Layout
          navbar={navbar}
          pageMap={await getPageMap()}
          docsRepositoryBase="https://github.com/sagi5060/agentdeck/tree/dev/docs-site"
          footer={footer}
          sidebar={{ autoCollapse: true }}
        >
          {children}
        </Layout>
      </body>
    </html>
  )
}
