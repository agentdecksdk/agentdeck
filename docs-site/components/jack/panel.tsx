'use client'

/**
 * Jack  -  the docs-native assistant panel (#219).
 *
 * Talks to `examples/jack`'s own `POST /ask`, which streams the run's canonical events.
 * There is no translation layer on either side: what this component switches on is the same
 * `event.kind` a Python consumer reading the run back would switch on.
 */

import { createPortal } from 'react-dom'
import { usePathname } from 'next/navigation'
import { useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { AnswerLink } from './answer-link'
import { Mark } from '@/components/site/mark'
import { jackCitationsPlugin } from './citations'
import { JackUnavailable, askJack } from './stream'

type Turn = { question: string; answer: string; reading: string[] }

/** `/concepts/agents/` -> `concepts/agents`, matching the slug `read_doc` is asked for. */
function slugOf(pathname: string): string {
  const trimmed = pathname.replace(/^\/+|\/+$/g, '')
  return trimmed || 'index'
}

export function JackPanel({ validSlugs }: { validSlugs: string[] }) {
  const slugs = useMemo(() => new Set(validSlugs), [validSlugs])
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

  // The page makes room for the panel rather than being covered by it: the class is on the root so
  // the padding applies to everything in flow, the sticky bar included.
  useEffect(() => {
    document.documentElement.classList.toggle('ask-open', open)
    return () => document.documentElement.classList.remove('ask-open')
  }, [open])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    await ask(question)
  }

  // Takes the question rather than reading state, so a suggestion can fire one on click: setting
  // the input first and submitting after would send whatever the previous render still held.
  async function ask(text: string) {
    const asked = text.trim()
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

  // The launcher stays in the bar while the panel is open: it is the toggle, so it cannot be the
  // thing the panel replaces.
  const launcher = (
    <button
      className={`ask-launch ${open ? 'is-open' : ''}`}
      onClick={() => setOpen(!open)}
      aria-expanded={open}
      aria-label="Ask Jack"
    >
      <AskIcon />
      <span className="ask-launch__label">Ask Jack</span>
    </button>
  )

  if (!open) return launcher

  // The panel is `position: fixed` against the viewport, and the bar it launches from carries
  // `backdrop-filter: blur(8px)`. A backdrop filter makes an element a containing block for its
  // fixed descendants, so rendered in place the panel resolved `top/bottom/right` against a 56px
  // header: 480x55 hanging off the bar, with its own head at y:-32. A portal is what puts it back
  // on the viewport.
  return (
    <>
      {launcher}
      {createPortal(
      <aside className="ask-panel" aria-label="Ask Jack">
      <header className="ask-head">
        <strong>Ask Jack</strong>
        <span className="ask-page">{slugOf(pathname)}</span>
        <button onClick={() => setOpen(false)} aria-label="Close">×</button>
      </header>

      <div className="ask-transcript" ref={transcript}>
        {turns.length === 0 && (
          <div className="ask-empty">
            <span className="ask-empty__mark">
              <Mark size={34} />
            </span>
            <p className="ask-empty__title">What can I help with?</p>
            <p className="ask-empty__note">
              Answers are read out of the documentation, not recalled: if it is not written down,
              you will be told so.
            </p>
            <div className="ask-empty__suggestions">
              {SUGGESTIONS.map(suggestion => (
                <button key={suggestion} onClick={() => void ask(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((turn, at) => (
          <div key={at} className="ask-turn">
            <p className="ask-question">{turn.question}</p>
            {turn.reading.length > 0 && <p className="ask-reading">{turn.reading.join(' · ')}</p>}
            <div className="ask-answer">
              <Markdown
                remarkPlugins={[remarkGfm, [jackCitationsPlugin, slugs]]}
                components={{ a: AnswerLink }}
              >
                {turn.answer}
              </Markdown>
            </div>
          </div>
        ))}
        {error && <p className="ask-error">{error}</p>}
      </div>

      <form className="ask-form" onSubmit={submit}>
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
      , document.body)}
    </>
  )
}

/** The brand spark, in `currentColor` so it takes the pill's own colour on hover. */
function AskIcon() {
  return (
    <svg viewBox="828.765 -64.697 257.229 257.086" fill="currentColor" aria-hidden="true">
      <path d="M 983.307 154.026 l -13.007 29.775 c -4.993 11.450 -20.837 11.450 -25.843 0.000 l -13.007 -29.775 c -11.582 -26.508 -32.405 -47.604 -58.379 -59.132 L 837.294 79.013 c -11.373 -5.045 -11.373 -21.606 0.000 -26.664 l 34.667 -15.384 C 898.614 25.135 919.804 3.268 931.190 -24.129 l 13.163 -31.736 c 4.889 -11.777 21.163 -11.777 26.052 0.000 L 983.569 -24.129 c 11.373 27.409 32.575 49.290 59.229 61.106 l 34.667 15.384 c 11.373 5.045 11.373 21.606 0.000 26.664 l -35.791 15.894 C 1015.712 106.435 994.876 127.532 983.307 154.026 Z" />
    </svg>
  )
}

const SUGGESTIONS = [
  'What does this page cover?',
  'Show me the smallest working example',
  'How is a Run different from a session?',
]
