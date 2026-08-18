import React from 'react'
import { BrandSpark } from './brand-icons'

interface BrandCalloutProps {
  type?: 'model' | 'runtime' | 'concept' | 'warning'
  title?: string
  children: React.ReactNode
}

export function BrandCallout({
  type = 'model',
  title,
  children
}: BrandCalloutProps) {
  const isRuntime = type === 'runtime'
  const isWarning = type === 'warning'
  const isModel = type === 'model' || type === 'concept'

  const defaultTitle = isModel
    ? 'AGENTDECK MODEL'
    : isRuntime
    ? 'RUNTIME BEHAVIOR'
    : isWarning
    ? 'IMPORTANT'
    : 'AGENTDECK NOTE'

  const displayTitle = title || defaultTitle

  return (
    <div className={`brand-callout ${type}`}>
      <div className="callout-header">
        <BrandSpark size={14} className={`callout-spark ${type}`} />
        <span className="callout-title">{displayTitle}</span>
      </div>
      <div className="callout-content">{children}</div>
    </div>
  )
}
