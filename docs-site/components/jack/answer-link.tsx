'use client'

import type { ReactNode } from 'react'
import Link from 'next/link'

/** Shared by both Jack surfaces (the landing page's live panel and the docs-page ask panel). A
 * citation link is always same-site (`jackCitationsPlugin` only ever emits a leading-slash
 * href); anything else is a real external link Jack didn't invent, left as a plain anchor. */
export function AnswerLink({ href, children }: { href?: string; children?: ReactNode }) {
  if (href?.startsWith('/')) return <Link href={href}>{children}</Link>
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  )
}
