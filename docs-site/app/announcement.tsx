import { Banner } from 'fumadocs-ui/components/banner'
import type { ReactNode } from 'react'

/** The announcement bar: one static line, brand blue, the stylized A as a faded watermark behind
 *  it. A new announcement is a new `id`, `href` and line: the `id` is what a reader's dismissal is
 *  remembered against, so a fresh one reappears for everybody. */
export function Announcement({
  id,
  href,
  cta = "See what's new",
  children,
}: {
  id: string
  href: string
  cta?: string
  children: ReactNode
}) {
  return (
    <Banner id={`agentdeck-${id}`}>
      <a className="ad-announce" href={href}>
        <Spark />
        <span className="ad-announce__claim">{children}</span>
        <span className="ad-announce__cta">
          {cta}
          <Arrow />
        </span>
      </a>
    </Banner>
  )
}

/** The spark, from the refined master `docs/brand/refine-brand/agentdeck-spark-master.svg`, in its
 *  Ace Red from the palette: the bar's one accent, and the only thing on it that is not blue or
 *  white. */
function Spark() {
  return (
    <svg className="ad-announce__spark" viewBox="828.765 -64.697 257.229 257.086" aria-hidden="true">
      <path d="M 983.307 154.026 l -13.007 29.775 c -4.993 11.450 -20.837 11.450 -25.843 0.000 l -13.007 -29.775 c -11.582 -26.508 -32.405 -47.604 -58.379 -59.132 L 837.294 79.013 c -11.373 -5.045 -11.373 -21.606 0.000 -26.664 l 34.667 -15.384 C 898.614 25.135 919.804 3.268 931.190 -24.129 l 13.163 -31.736 c 4.889 -11.777 21.163 -11.777 26.052 0.000 L 983.569 -24.129 c 11.373 27.409 32.575 49.290 59.229 61.106 l 34.667 15.384 c 11.373 5.045 11.373 21.606 0.000 26.664 l -35.791 15.894 C 1015.712 106.435 994.876 127.532 983.307 154.026 Z" />
    </svg>
  )
}

/** Drawn rather than the arrow glyph, whose stroke and baseline are the font's, not the label's. */
function Arrow() {
  return (
    <svg
      className="ad-announce__arrow"
      viewBox="0 0 16 12"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M 1 6 H 14" />
      <path d="M 9.5 1.5 L 14 6 L 9.5 10.5" />
    </svg>
  )
}
