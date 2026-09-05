'use client'

import React, { useState } from 'react'
import { BrandSpark, BrandCardMark } from '@/components/ui/brand-icons'

export interface SparkMarkerProps {
  kind?: 'inactive' | 'active' | 'live'
  size?: number
  className?: string
}

export function SparkMarker({ kind = 'inactive', size = 14, className = '' }: SparkMarkerProps) {
  return (
    <span className={`spark-marker ${kind} ${className}`}>
      <BrandSpark size={size} />
    </span>
  )
}

export interface DeckSurfaceProps {
  children: React.ReactNode
  variant?: 'elevated' | 'sunken' | 'accent'
  className?: string
}

export function DeckSurface({ children, variant = 'elevated', className = '' }: DeckSurfaceProps) {
  return <div className={`deck-surface ${variant} ${className}`}>{children}</div>
}

export interface AgentDeckCodeBlockProps {
  filename?: string
  lang?: string
  children: React.ReactNode
}

export function AgentDeckCodeBlock({ filename = 'example.py', lang = 'Python', children }: AgentDeckCodeBlockProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    if (typeof window !== 'undefined') {
      const codeText = typeof children === 'string' ? children : ''
      if (codeText) navigator.clipboard.writeText(codeText)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    }
  }

  return (
    <div className="agentdeck-code-block">
      <div className="code-block-header">
        <div className="code-block-file">
          <BrandSpark size={12} className="code-spark" />
          <span className="file-name">{filename}</span>
        </div>
        <div className="code-block-actions">
          <span className="code-lang">{lang}</span>
          <button className="code-copy-btn" onClick={handleCopy}>
            {copied ? 'COPIED' : 'COPY'}
          </button>
        </div>
      </div>
      <div className="code-block-body">{children}</div>
    </div>
  )
}

export interface TimelineStep {
  id: string
  label: string
  status?: 'done' | 'active' | 'pending' | 'waiting'
}

export interface RunTimelineProps {
  steps: TimelineStep[]
}

export function RunTimeline({ steps }: RunTimelineProps) {
  return (
    <div className="run-timeline-component">
      <div className="timeline-rail">
        {steps.map((step, idx) => {
          const isDone = step.status === 'done'
          const isActive = step.status === 'active'
          const isWaiting = step.status === 'waiting'
          const kind = isWaiting ? 'live' : isDone || isActive ? 'active' : 'inactive'

          return (
            <React.Fragment key={step.id}>
              {idx > 0 && <div className={`rail-connector ${isDone || isActive ? 'active' : ''}`} />}
              <div className={`rail-node ${step.status || 'pending'}`}>
                <BrandSpark size={12} className={`node-spark ${kind}`} />
                <span className="node-label">{step.label}</span>
              </div>
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}

export interface StepRailProps {
  steps: {
    num: string
    title: string
    content: React.ReactNode
  }[]
}

export function StepRail({ steps }: StepRailProps) {
  return (
    <div className="step-rail-container">
      {steps.map((s, idx) => (
        <div key={s.num} className="step-rail-item">
          <div className="step-rail-header">
            <BrandSpark size={14} className={`step-spark ${idx === 0 ? 'active' : 'inactive'}`} />
            <span className="step-num">{s.num}</span>
            <span className="step-sep">:</span>
            <span className="step-title">{s.title}</span>
          </div>
          <div className="step-rail-body">{s.content}</div>
        </div>
      ))}
    </div>
  )
}
