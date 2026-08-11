import type { Metadata } from 'next'
import { Inter, Poppins } from 'next/font/google'
import { Footer, Layout, Navbar } from 'nextra-theme-docs'
import { Head } from 'nextra/components'
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
    default: 'AgentDeck',
    template: '%s | AgentDeck'
  },
  description: 'Build the agent. Own the runtime.'
}

const navbar = (
  <Navbar
    logo={<strong style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em' }}>AgentDeck</strong>}
    projectLink="https://github.com/sagi5060/agentdeck"
  />
)

const footer = (
  <Footer>
    AgentDeck · Build the agent. Own the runtime.
  </Footer>
)

export default async function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" dir="ltr" className={`${body.variable} ${display.variable}`} suppressHydrationWarning>
      <Head faviconGlyph="A" backgroundColor={{ light: '#fbf7f1', dark: '#0f172a' }} color={{ hue: { light: 211, dark: 345 }, saturation: { light: 87, dark: 67 } }} />
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
