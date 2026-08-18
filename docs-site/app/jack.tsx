'use client'

/**
 * Jack  -  the docs-native assistant panel (#219).
 *
 * Talks to `examples/jack`'s own `POST /ask`, which streams the run's canonical events.
 * There is no translation layer on either side: what this component switches on is the same
 * `event.kind` a Python consumer reading the run back would switch on.
 */

import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Build-time, not runtime: the bundle is static, so this is baked in when the site is built.
// Defaults to localhost so `npm run dev` works against a locally running backend with no setup.
const API = process.env.NEXT_PUBLIC_AGENTDECK_API_URL || 'http://localhost:8100'

type Turn = { question: string; answer: string; reading: string[] }

/** `/concepts/agents/` -> `concepts/agents`, matching the slug `read_doc` is asked for. */
function slugOf(pathname: string): string {
  const trimmed = pathname.replace(/^\/+|\/+$/g, '')
  return trimmed || 'index'
}

export function JackPanel() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The reader's selection, captured as they make it: opening the panel moves focus and clears
  // it, so reading it at submit time would always find nothing.
  const selection = useRef('')
  const session = useRef(`reader-${Math.random().toString(36).slice(2)}`)
  const transcript = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const remember = () => {
      const text = window.getSelection()?.toString().trim()
      if (text) selection.current = text
    }
    document.addEventListener('selectionchange', remember)
    return () => document.removeEventListener('selectionchange', remember)
  }, [])

  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight })
  }, [turns])

  async function ask(event: React.FormEvent) {
    event.preventDefault()
    const asked = question.trim()
    if (!asked || busy) return
    setQuestion('')
    setError(null)
    setBusy(true)
    const index = turns.length
    setTurns(previous => [...previous, { question: asked, answer: '', reading: [] }])

    const update = (change: (turn: Turn) => Turn) =>
      setTurns(previous => previous.map((turn, at) => (at === index ? change(turn) : turn)))

    try {
      const response = await fetch(`${API}/ask`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          question: asked,
          page: slugOf(pathname),
          selection: selection.current || null,
          session_id: session.current
        })
      })
      if (!response.ok || !response.body) throw new Error(`the assistant answered ${response.status}`)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE frames are blank-line separated; the tail is whatever has not arrived in full yet.
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''
        for (const frame of frames) {
          const line = frame.trim()
          if (!line.startsWith('data: ')) continue
          const { kind, payload } = JSON.parse(line.slice(6))
          if (kind === 'text.delta') {
            update(turn => ({ ...turn, answer: turn.answer + payload.text }))
          } else if (kind === 'tool.call.started') {
            const target = payload.args?.slug || payload.args?.query || ''
            update(turn => ({ ...turn, reading: [...turn.reading, `${payload.tool}: ${target}`] }))
          } else if (kind === 'run.failed') {
            throw new Error(payload.message || 'the run failed')
          }
        }
      }
      selection.current = ''
    } catch (failure) {
      // Unreachable is the common case in development, and saying so beats a spinner that
      // never resolves. The panel stays usable; the transcript keeps what it already had.
      setError(
        failure instanceof TypeError
          ? `Jack is not reachable at ${API}. Start it with \`uvicorn jack.server:app --port 8100\`.`
          : String(failure)
      )
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button className="ask-launch" onClick={() => setOpen(true)} aria-label="Ask Jack">
        Ask Jack
      </button>
    )
  }

  return (
    <aside className="ask-panel" aria-label="Ask Jack">
      <header className="ask-head">
        <strong>Ask Jack</strong>
        <span className="ask-page">{slugOf(pathname)}</span>
        <button onClick={() => setOpen(false)} aria-label="Close">×</button>
      </header>

      <div className="ask-transcript" ref={transcript}>
        {turns.length === 0 && (
          <p className="ask-hint">
            Ask about this page or anything else in the docs. Answers are read out of the
            documentation, not recalled  -  if it is not written down, you will be told so.
          </p>
        )}
        {turns.map((turn, at) => (
          <div key={at} className="ask-turn">
            <p className="ask-question">{turn.question}</p>
            {turn.reading.length > 0 && <p className="ask-reading">{turn.reading.join(' · ')}</p>}
            <div className="ask-answer">
              <Markdown remarkPlugins={[remarkGfm]}>{turn.answer}</Markdown>
            </div>
          </div>
        ))}
        {error && <p className="ask-error">{error}</p>}
      </div>

      <form className="ask-form" onSubmit={ask}>
        <input
          value={question}
          onChange={event => setQuestion(event.target.value)}
          placeholder={busy ? 'Reading the docs…' : 'How do I create an agent?'}
          disabled={busy}
          autoFocus
        />
        <button type="submit" disabled={busy || !question.trim()}>Ask</button>
      </form>
    </aside>
  )
}
