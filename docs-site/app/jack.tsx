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

import { JackUnavailable, askJack } from './jack-stream'

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
      for await (const frame of askJack({
        question: asked,
        page: slugOf(pathname),
        selection: selection.current || null,
        session_id: session.current
      })) {
        const { kind, payload } = frame
        if (kind === 'text.delta') {
          update(turn => ({ ...turn, answer: turn.answer + payload.text }))
        } else if (kind === 'tool.call.started') {
          const target = payload.args?.slug || payload.args?.query || ''
          update(turn => ({ ...turn, reading: [...turn.reading, `${payload.tool}: ${target}`] }))
        } else if (kind === 'run.failed') {
          throw new JackUnavailable(payload.message || 'the run failed')
        }
      }
      selection.current = ''
    } catch (failure) {
      // The panel stays usable and the transcript keeps what it already had.
      setError(failure instanceof JackUnavailable ? failure.message : String(failure))
    } finally {
      setBusy(false)
    }
  }

  // The landing page runs Jack itself, in a section built around him, so a launcher floating over
  // it would be a second way to reach the same agent on the one page that already has him.
  if (slugOf(pathname) === 'index') return null

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
