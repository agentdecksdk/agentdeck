'use client'

import { usePathname } from 'next/navigation'
import { useState } from 'react'

/**
 * "Was this page helpful?" in the table of contents rail.
 *
 * A click has nowhere to be stored: the docs site is a static export with no analytics endpoint,
 * so the verdict is carried into a prefilled issue rather than counted here. The follow-up link is
 * the whole point of the widget, not a footnote to it.
 */
export function PageFeedback() {
  const pathname = usePathname()
  const [verdict, setVerdict] = useState<'helpful' | 'not helpful' | null>(null)

  if (verdict) {
    const issue =
      'https://github.com/agentdecksdk/agentdeck/issues/new?labels=documentation' +
      `&title=${encodeURIComponent(`Docs feedback: ${pathname}`)}` +
      `&body=${encodeURIComponent(`Page: ${pathname}\nVerdict: ${verdict}\n\nWhat would have helped?\n`)}`
    return (
      <div className="page-feedback">
        <p className="page-feedback__prompt">Thanks.</p>
        <a className="page-feedback__more" href={issue} target="_blank" rel="noreferrer">
          Tell us more
        </a>
      </div>
    )
  }

  return (
    <div className="page-feedback">
      <p className="page-feedback__prompt">Was this page helpful?</p>
      <div className="page-feedback__votes">
        <button onClick={() => setVerdict('helpful')} aria-label="Yes, this page was helpful">
          <Thumb />
        </button>
        <button onClick={() => setVerdict('not helpful')} aria-label="No, this page was not helpful">
          <Thumb down />
        </button>
      </div>
    </div>
  )
}

function Thumb({ down = false }: { down?: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.3}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={down ? { transform: 'rotate(180deg)' } : undefined}
      aria-hidden="true"
    >
      <path d="M5.4 14V6.8l3-5.2c.9.2 1.5 1 1.5 2v2.6h3.1c.8 0 1.4.7 1.2 1.5l-1 4.4c-.1.6-.6 1-1.2 1z" />
      <path d="M5.4 6.8H2.6c-.4 0-.7.3-.7.7v5.8c0 .4.3.7.7.7h2.8" />
    </svg>
  )
}
