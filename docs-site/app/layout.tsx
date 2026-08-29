import type { Metadata } from 'next'
import { Inter, Poppins } from 'next/font/google'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
import { Mark } from './mark'
import { getPageMap } from 'nextra/page-map'
import type { ReactNode } from 'react'
import { docsSlugs } from './docs-slugs'
import { CURRENT_VERSION } from './generated-version'
import { JackPanel } from './jack'
import { SITE } from './site'
import 'nextra-theme-docs/style.css'
import './brand.css'
import './landing.css'
import './hero.css'
import './jack.css'

// Self-hosted at build time  -  the static export makes no external font request.
const body = Inter({ subsets: ['latin'], variable: '--font-body', display: 'swap' })
const display = Poppins({ subsets: ['latin'], weight: ['500', '600'], variable: '--font-display', display: 'swap' })

export const metadata: Metadata = {
  // Every relative URL in metadata  -  canonical tags, OG images  -  resolves against this, so the
  // whole site moves domain by changing one env var rather than by editing every page.
  metadataBase: new URL(SITE),
  alternates: { canonical: './' },
  openGraph: {
    type: 'website',
    siteName: 'AgentDeck SDK',
    url: './',
    title: 'AgentDeck SDK  -  a production runtime for AI agents',
    description:
      'Durable human-in-the-loop approvals, sessions, streaming, run control and one ordered '
      + 'event log per run  -  wrapping the OpenAI Agents SDK rather than replacing it.'
  },
  twitter: { card: 'summary_large_image', title: 'AgentDeck SDK' },
  title: {
    default: 'AgentDeck SDK  -  a production runtime for AI agents',
    template: '%s | AgentDeck SDK'
  },
  description:
    'AgentDeck SDK adds composition and a production runtime around agents you already have  -  '
    + 'durable human-in-the-loop approvals, sessions, streaming, run control and one ordered '
    + 'event log per run  -  wrapping the OpenAI Agents SDK rather than replacing it.'
}

const navbar = (
  <Navbar
    logo={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
        <Mark />
        <strong style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em' }}>AgentDeck</strong>
      </span>
    }
    projectLink="https://github.com/agentdecksdk/agentdeck"
  />
)

const footer = (
  <Footer>
    AgentDeck SDK v{CURRENT_VERSION} · Compose. Observe. Ship.
  </Footer>
)

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" dir="ltr" className={`${body.variable} ${display.variable}`} suppressHydrationWarning>
      <Head backgroundColor={{ light: '#fafbfe', dark: '#0b1220' }} color={{ hue: { light: 222.9, dark: 221.1 }, saturation: { light: 100, dark: 100 }, lightness: { light: 57.3, dark: 78.8 } }} />
      <body style={{ fontFamily: 'var(--font-body), sans-serif' }}>
        {/* What the site *is*, in the form a search engine reads. Without it a crawler has to
            infer the entry point from links alone, and a documentation page that answers a query
            well can end up standing in for the site itself. */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              '@context': 'https://schema.org',
              '@graph': [
                {
                  '@type': 'WebSite',
                  '@id': `${SITE}/#website`,
                  url: `${SITE}/`,
                  name: 'AgentDeck SDK',
                  description:
                    'Build agents, tools and workflows as normal software. AgentDeck gives them '
                    + 'one execution model you can observe, control and extend.'
                },
                {
                  '@type': 'SoftwareSourceCode',
                  name: 'AgentDeck SDK',
                  description:
                    'A declarative runtime harness for multi-agent systems, wrapping the OpenAI '
                    + 'Agents SDK rather than replacing it.',
                  codeRepository: 'https://github.com/agentdecksdk/agentdeck',
                  programmingLanguage: 'Python',
                  license: 'https://opensource.org/licenses/MIT',
                  isPartOf: { '@id': `${SITE}/#website` }
                }
              ]
            })
          }}
        />
        <Layout
          navbar={navbar}
          pageMap={await getPageMap()}
          docsRepositoryBase="https://github.com/agentdecksdk/agentdeck/tree/dev/docs-site"
          footer={footer}
          sidebar={{ autoCollapse: true }}
        >
          {children}
        </Layout>
        <JackPanel validSlugs={docsSlugs()} />
      </body>
    </html>
  )
}
