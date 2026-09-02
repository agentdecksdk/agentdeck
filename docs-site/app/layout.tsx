import type { Metadata } from 'next'
import { Inter, Poppins } from 'next/font/google'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head, Search } from 'nextra/components'
import { Agentation } from 'agentation'
import { Announcement } from './announcement'
import { PageFeedback } from './page-feedback'
import { TocActive } from './toc-active'
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
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.55rem' }}>
        <Mark size={26} />
        <strong style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em' }}>AgentDeck</strong>
      </span>
    }
    projectLink="https://github.com/agentdecksdk/agentdeck"
    projectIcon={<GitHubMark />}
  >
    <JackPanel validSlugs={docsSlugs()} />
  </Navbar>
)

/** Primer's `mark-github-16`, drawn for this size: the theme's default is a 24px glyph scaled down,
 *  which is where the muddy edges at 18px came from. */
function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656" />
    </svg>
  )
}

const footer = (
  <Footer>
    AgentDeck SDK v{CURRENT_VERSION} · Compose. Observe. Ship.
  </Footer>
)

const banner = (
  <Announcement id="v6-launch" href="/meet-agentdeck/whats-new-6">
    AgentDeck 6.0 is here: serve one deck over HTTP, AG-UI or the terminal.
  </Announcement>
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
          search={<Search placeholder="Search docs" />}
          pageMap={await getPageMap()}
          docsRepositoryBase="https://github.com/agentdecksdk/agentdeck/tree/dev/docs-site"
          footer={footer}
          // Nextra collapses from level 2 by default, which leaves every section open at once;
          // from level 1 only the section being read is expanded, and autoCollapse closes the last.
          sidebar={{ autoCollapse: true, defaultMenuCollapseLevel: 1 }}
          toc={{ extraContent: <PageFeedback /> }}
          banner={banner}
        >
          {children}
        </Layout>
        <TocActive />
        {/* Annotate the running site in the browser; the overlay talks to the agent over the
            agentation MCP server. Dead code in a production build, where NODE_ENV is inlined. */}
        {process.env.NODE_ENV === 'development' && <Agentation />}
      </body>
    </html>
  )
}
