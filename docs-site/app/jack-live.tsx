'use client'

/**
 * Jack, live, with his execution tree beside him.
 *
 * The tree is built from the run's canonical events and nothing else. No node appears that no
 * event produced, and no node completes on a timer: a tool is running from `tool.call.started`
 * until the run produces text or finishes, both of which prove every outstanding call returned.
 * That is why two tools dispatched together are both shown running, which is what happened.
 *
 * `tool.call.completed` is deliberately not on this wire (it carries `result_preview`, which a
 * raising tool would fill with its own exception text), so per-tool completion is inferred from
 * the events that are public rather than faked from the ones that are not.
 */

import { useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { TreeFigure, type TreeNode } from './deck-figure'
import { JackUnavailable, askJack } from './jack-stream'

const EXAMPLES = [
  'How do I wrap my LangGraph agent?',
  'How does run pause and resume work?',
  'Explain AgentDeck context injection',
  'What is in the latest release?'
]

type ToolNode = { name: string; detail: string; state: 'running' | 'done' }

interface RunView {
  runId: string
  agent: string
  tools: ToolNode[]
  state: 'running' | 'done' | 'failed'
}

/** The one argument worth showing: what the tool was actually asked for. */
function argOf(args: Record<string, unknown> | undefined): string {
  const value = args?.query ?? args?.slug ?? args?.subject
  return typeof value === 'string' ? value : ''
}

function asTree(run: RunView): TreeNode {
  return {
    name: 'Run',
    kind: 'run',
    detail: run.runId.slice(0, 12),
    state: run.state,
    children: [
      {
        name: run.agent,
        kind: 'agent',
        state: run.state,
        children: run.tools.map(tool => ({
          name: tool.name,
          kind: 'tool' as const,
          detail: tool.detail,
          state: tool.state
        }))
      }
    ]
  }
}

export function JackLive() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [asked, setAsked] = useState<string | null>(null)
  const [run, setRun] = useState<RunView | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // One conversation per visitor. The backend's quota counts sessions per client per day, so a
  // fresh id on every question would spend the whole allowance in three turns.
  const session = useRef(`landing-${Math.random().toString(36).slice(2)}`)

  async function ask(text: string) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    setQuestion('')
    setAsked(trimmed)
    setAnswer('')
    setError(null)
    setRun(null)
    setBusy(true)

    // Every outstanding tool has returned by the time the run produces prose or terminates.
    const settle = () =>
      setRun(current =>
        current ? { ...current, tools: current.tools.map(tool => ({ ...tool, state: 'done' as const })) } : current
      )

    try {
      for await (const event of askJack({ question: trimmed, session_id: session.current })) {
        if (event.kind === 'run.started') {
          setRun({ runId: event.run_id, agent: event.payload.invocable, tools: [], state: 'running' })
        } else if (event.kind === 'tool.call.started') {
          setRun(current =>
            current
              ? {
                  ...current,
                  tools: [...current.tools, { name: event.payload.tool, detail: argOf(event.payload.args), state: 'running' }]
                }
              : current
          )
        } else if (event.kind === 'text.delta') {
          settle()
          setAnswer(previous => previous + event.payload.text)
        } else if (event.kind === 'run.completed') {
          settle()
          setRun(current => (current ? { ...current, state: 'done' } : current))
        } else if (event.kind === 'run.failed') {
          setRun(current => (current ? { ...current, state: 'failed' } : current))
          throw new JackUnavailable(event.payload.message || 'The run failed.')
        }
      }
    } catch (failure) {
      setError(failure instanceof JackUnavailable ? failure.message : String(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="jack-live">
      <div className="jack-chat">
        <div className="jack-transcript">
          {asked === null && !error && (
            <div className="jack-examples">
              <p className="jack-examples-label">Try one</p>
              {EXAMPLES.map(example => (
                <button key={example} type="button" className="jack-example" onClick={() => ask(example)}>
                  {example}
                </button>
              ))}
            </div>
          )}
          {asked !== null && <p className="jack-question">{asked}</p>}
          {answer && (
            <div className="jack-answer">
              <Markdown remarkPlugins={[remarkGfm]}>{answer}</Markdown>
            </div>
          )}
          {error && <p className="jack-error">{error}</p>}
        </div>
        <form
          className="jack-form"
          onSubmit={event => {
            event.preventDefault()
            ask(question)
          }}
        >
          <input
            value={question}
            onChange={event => setQuestion(event.target.value)}
            placeholder={busy ? 'Reading the docs' : 'Ask anything'}
            disabled={busy}
            aria-label="Ask Jack a question"
          />
          <button type="submit" disabled={busy || !question.trim()}>
            Ask
          </button>
        </form>
      </div>

      <div className="jack-runtime">
        <p className="jack-runtime-label">
          Execution
          {run && <span className={`jack-runtime-state is-${run.state}`}>{run.state}</span>}
        </p>
        {run ? (
          <TreeFigure root={asTree(run)} live />
        ) : (
          <p className="jack-runtime-idle">
            Ask Jack something. Every node that appears here is an event his run emitted.
          </p>
        )}
      </div>
    </div>
  )
}
