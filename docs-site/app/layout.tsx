import type { Metadata } from 'next'
import { Inter, Poppins } from 'next/font/google'
import { RootProvider } from 'fumadocs-ui/provider/next'
import { Agentation } from 'agentation'
import { Announcement } from './announcement'
import PagefindSearchDialog from './search-dialog'
import type { ReactNode } from 'react'
import { CURRENT_VERSION } from './generated-version'
import { SITE } from './site'
import './global.css'
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

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" dir="ltr" className={`${body.variable} ${display.variable}`} suppressHydrationWarning>
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
        <RootProvider search={{ SearchDialog: PagefindSearchDialog }}>
          <Announcement id="v6-launch" href="/meet-agentdeck/whats-new-6">
            AgentDeck 6.0 is here: serve one deck over HTTP, AG-UI or the terminal.
          </Announcement>
          {children}
          <footer className="site-footer">
            AgentDeck SDK v{CURRENT_VERSION} · Compose. Observe. Ship.
          </footer>
        </RootProvider>
        {/* Annotate the running site in the browser; the overlay talks to the agent over the
            agentation MCP server. Dead code in a production build, where NODE_ENV is inlined. */}
        {process.env.NODE_ENV === 'development' && <Agentation />}
      </body>
    </html>
  )
}
