'use client'

import { useState } from 'react'

// The distribution is `agentdeck-sdk`; the import package is `agentdeck`. PyPI refused
// `agentdeck` as too similar to a squatted placeholder, so the install line is not the import.
const INSTALL = 'pip install agentdeck-sdk'

export function InstallLine() {
  const [copied, setCopied] = useState(false)

  return (
    <button
      type="button"
      className="install-line"
      onClick={() => {
        navigator.clipboard.writeText(INSTALL)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
    >
      <span className="install-prompt" aria-hidden="true">
        $
      </span>
      <code>{INSTALL}</code>
      <span className="install-action">{copied ? 'copied' : 'copy'}</span>
    </button>
  )
}
