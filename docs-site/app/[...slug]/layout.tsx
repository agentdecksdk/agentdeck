import type { ReactNode } from 'react'
import { DocsLayout } from 'fumadocs-ui/layouts/notebook'
import { ThemeSwitch } from 'fumadocs-ui/layouts/shared/slots/theme-switch'
import { JackPanel } from '../jack'
import { Mark } from '../mark'
import { pageSlugs, source } from '../source'
import { CURRENT_VERSION } from '../generated-version'

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={source.getPageTree()}
      /* v6.0.3 put the theme control at the foot of the sidebar, not in the bar, and the bar's
         right end was search, the assistant, then the repo link. */
      themeSwitch={{ enabled: false }}
      /* `collapsible` is what puts the collapse trigger in the bar; v6.0.3 had no such control
         there, and the sidebar is the page's spine rather than something to fold away. */
      sidebar={{ footer: <ThemeSwitch key="theme-switch" mode="light-dark-system" />, collapsible: false }}
      nav={{
        /* The v6.0.3 arrangement: a real top bar carrying the mark, the assistant and the repo
           link, with the sidebar left to the page tree. `layouts/notebook` is fumadocs-ui's own
           layout for this shape; `layouts/docs` stacks all of it into the sidebar instead. */
        mode: 'top',
        /* The assistant belongs in the bar at every width. As a `links` entry fumadocs-ui moves
           it into the sidebar below `lg`; as nav children it stays put, which is where v6.0.3
           had it. */
        children: (
          <span key="bar-actions" className="nav-actions">
            <JackPanel validSlugs={pageSlugs()} />
            <a
              className="nav-actions__repo"
              href="https://github.com/agentdecksdk/agentdeck"
              aria-label="GitHub"
            >
              <GitHubMark />
            </a>
          </span>
        ),
        title: (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.55rem' }}>
            <Mark size={26} />
            <strong style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em' }}>AgentDeck</strong>
            <span className="text-xs docs-version">v{CURRENT_VERSION}</span>
          </span>
        )
      }}
    >
      {children}
    </DocsLayout>
  )
}

/** Primer's `mark-github-16`, drawn for this size: a 24px glyph scaled down is where the muddy
 *  edges at 18px came from. */
function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656" />
    </svg>
  )
}
