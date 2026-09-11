import { CURRENT_VERSION } from '@/lib/version'
import { cn } from '@/lib/utils'

/** The site footer.
 *
 *  Inside the docs grid rather than a sibling of it. As a sibling it ended below `.ad-shell`, and
 *  a sticky element is bounded by its own grid area: at full scroll the sidebar was pushed up by
 *  exactly the footer's height and slid under the top bar. */
export function SiteFooter({ className }: { className?: string }) {
  return (
    <footer className={cn('site-footer', className)}>
      {/* The claim is README.md:7, verbatim. */}
      AgentDeck SDK v{CURRENT_VERSION} · Agentic software should feel like software.
    </footer>
  )
}
