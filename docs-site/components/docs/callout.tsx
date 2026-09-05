import React from 'react'
import { BrandSpark } from '@/components/ui/brand-icons'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'

interface BrandCalloutProps {
  type?: 'model' | 'runtime' | 'concept' | 'warning'
  title?: string
  children: React.ReactNode
}

const DEFAULT_TITLE = {
  model: 'AGENTDECK MODEL',
  concept: 'AGENTDECK MODEL',
  runtime: 'RUNTIME BEHAVIOR',
  warning: 'IMPORTANT'
} as const

/** A note the reader should not skim past.
 *
 *  Built on `ui/alert` for the structure a hand-rolled div did not have: `role="alert"` and the
 *  title/description grid that keeps a wrapped second line aligned under the first. The surface
 *  stays the brand's, so `.brand-callout` still paints it. */
export function BrandCallout({ type = 'model', title, children }: BrandCalloutProps) {
  return (
    <Alert className={`brand-callout ${type}`}>
      <BrandSpark size={14} className={`callout-spark ${type}`} />
      <AlertTitle className="callout-title">{title || DEFAULT_TITLE[type]}</AlertTitle>
      <AlertDescription className="callout-content">{children}</AlertDescription>
    </Alert>
  )
}
