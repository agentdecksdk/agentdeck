import type { Metadata } from 'next'
import { Inter, Poppins } from 'next/font/google'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
import { Mark } from './mark'
import { getPageMap } from 'nextra/page-map'
import type { ReactNode } from 'react'
import { AskAgentDeck } from './ask-agentdeck'
import 'nextra-theme-docs/style.css'
import './brand.css'
import './ask-agentdeck.css'

// Self-hosted at build time — the static export makes no external font request.
const body = Inter({ subsets: ['latin'], variable: '--font-body', display: 'swap' })
const display = Poppins({ subsets: ['latin'], weight: ['500', '600'], variable: '--font-display', display: 'swap' })

export const metadata: Metadata = {
  title: {
    default: 'AgentDeck SDK — a production runtime for AI agents',
    template: '%s | AgentDeck SDK'
  },
  description:
    'AgentDeck SDK adds composition and a production runtime around agents you already have — '
    + 'durable human-in-the-loop approvals, sessions, streaming, run control and one ordered '
    + 'event log per run — wrapping the OpenAI Agents SDK and LangGraph rather than replacing them.'
}

const navbar = (
  <Navbar
    logo={
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
        <Mark />
        <strong style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em' }}>AgentDeck</strong>
      </span>
    }
    projectLink="https://github.com/sagi5060/agentdeck"
  />
)

const footer = (
  <Footer>
    AgentDeck SDK · Compose. Observe. Ship.
  </Footer>
)

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" dir="ltr" className={`${body.variable} ${display.variable}`} suppressHydrationWarning>
      <Head backgroundColor={{ light: '#fafbfe', dark: '#0b1220' }} color={{ hue: { light: 222.9, dark: 221.1 }, saturation: { light: 100, dark: 100 }, lightness: { light: 57.3, dark: 78.8 } }} />
      <body style={{ fontFamily: 'var(--font-body), sans-serif' }}>
        <Layout
          navbar={navbar}
          pageMap={await getPageMap()}
          docsRepositoryBase="https://github.com/sagi5060/agentdeck/tree/dev/docs-site"
          footer={footer}
          sidebar={{ autoCollapse: true }}
        >
          {children}
        </Layout>
        <AskAgentDeck />
      </body>
    </html>
  )
}
