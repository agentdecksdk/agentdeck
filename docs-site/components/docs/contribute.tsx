import React from 'react'
import { BrandSpark } from '@/components/ui/brand-icons'

type Difficulty = 'small' | 'medium' | 'advanced'
type Scope = 'example' | 'guide' | 'diagram' | 'troubleshooting' | 'integration'

interface ContributeProps {
  /** The one issue this invites. A label listing is not a first contribution. */
  issue: number
  difficulty: Difficulty
  scope: Scope
  /** What is missing, in one sentence, written for this page. */
  need: string
  /** Why it helps, in one sentence. */
  because: string
}

const REPO = 'https://github.com/agentdecksdk/agentdeck/issues'

export function Contribute({ issue, difficulty, scope, need, because }: ContributeProps) {
  return (
    <aside className="ad-contribute">
      <div className="ad-contribute__head">
        <BrandSpark size={12} className="ad-contribute__spark" />
        <span className="ad-contribute__label">Improve this page</span>
        <span className="ad-contribute__meta">
          {difficulty} · {scope}
        </span>
      </div>
      <p className="ad-contribute__body">
        {need} {because}
      </p>
      <a className="ad-contribute__link" href={`${REPO}/${issue}`}>
        Take this contribution <span className="ad-contribute__issue">#{issue}</span>
        <span aria-hidden="true"> →</span>
      </a>
    </aside>
  )
}
