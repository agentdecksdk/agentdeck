import React from 'react'
import Link from 'next/link'

import { TreeFigure, type TreeNode } from './deck-figure'
import { docsSlugs } from './docs-slugs'
import { JackLive } from './jack-live'
import { InstallLine } from './install-line'
import { Hero } from './hero'

// Read once at build time (this module never crosses into the client bundle); JackLive uses it
// to decide which of Jack's citations are real pages worth linking.
const DOC_SLUGS = docsSlugs()

/**
 * The landing page.
 *
 * It makes one claim, shows the code and the execution model behind it, and hands the reader the
 * agent actually running on this site. The step-by-step build of that agent lives at `/jack`,
 * where there is room for the reasoning behind each decision: repeating it here turned one
 * argument into a nine-part tutorial the reader had to finish before reaching the proof.
 */

export { Hero }

const REPO = 'https://github.com/agentdecksdk/agentdeck'
const JACK_SOURCE = `${REPO}/tree/dev/examples/jack`

type Status = 'shipped' | 'proposed'

/** Whether a code block is shipped or proposed, on the block. */
export function Snippet({ status, children }: { status: Status; children?: React.ReactNode }) {
  const shipped = status === 'shipped'
  return (
    <div className={`snippet is-${status}`}>
      <p className="snippet-head">
        <span className={`status-chip is-${status}`}>
          <span aria-hidden="true">{shipped ? '◆' : '◇'}</span>
          {status}
        </span>
      </p>
      {children}
    </div>
  )
}

/* ------------------------------------------------------------ the argument */

export function Foundation() {
  return (
    <section className="chapter is-plain">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <h2 className="chapter-title">Built to stay out of your way.</h2>
        <p className="chapter-lead">
          Agent systems accumulate infrastructure quickly, and the decisions that look small early
          are the hardest to change later. Execution, control, state, observability, reporting,
          interaction and integration already have their place in AgentDeck, and they were
          designed to work together: less machinery to build today, and nothing to retrofit
          around your application when you need the next one.
        </p>
      </div>
    </section>
  )
}

/* ------------------------------------------------------ code, and the model */

const TREE: TreeNode = {
  name: 'Run',
  kind: 'run',
  children: [
    {
      name: 'Jack',
      kind: 'agent',
      children: [
        { name: 'search_docs', kind: 'tool' },
        { name: 'read_doc', kind: 'tool' },
        { name: 'read_changelog', kind: 'tool' }
      ]
    }
  ]
}

const BUYS: [string, string][] = [
  ['Events', 'One ordered stream of what happened.'],
  ['Reporting', 'Progress and status, sent from inside the work.'],
  ['Control', 'Execution can be paused, resumed or cancelled.'],
  ['Interaction', 'Branches can wait for external input.'],
  ['State', 'Sessions that outlive a single call.'],
  ['Surfaces', 'Observers, HTTP and your UI read the same run.']
]

export function Model({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-wide">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <p className="chapter-eyebrow">The model</p>
        <h2 className="chapter-title">Simple code. Serious capabilities.</h2>
        <p className="chapter-lead">
          Your code stays about the behavior and the structure of your application. Tools do work,
          agents make decisions, workflows manage process, and a Deck is where they come together.
        </p>
        <div className="chapter-grid">
          <div className="chapter-code">{children}</div>
          <div className="chapter-figure">
            <TreeFigure
              root={TREE}
              caption="That code, running. Everything else attaches to this same model."
            />
          </div>
        </div>
        <dl className="tree-buys">
          {BUYS.map(([term, detail]) => (
            <div className="tree-buy" key={term}>
              <dt>{term}</dt>
              <dd>{detail}</dd>
            </div>
          ))}
        </dl>
        <p className="chapter-close">
          The complexity is still there. It just lives in the layer built for it.
        </p>
      </div>
    </section>
  )
}
/* --------------------------------------------------------------- real Jack */

export function MeetJack({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-wide is-live-section">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <p className="chapter-eyebrow">Jack, running</p>
        <h2 className="chapter-title">Anything you want to ask?</h2>
        <p className="chapter-lead">
          Jack is an AgentDeck developer agent running on this site. Ask him about the SDK,
          architecture, integration, or paste code you are working with. The tree beside him is
          his actual run: every node is an event the runtime emitted, in the order it emitted it.
        </p>
        <JackLive validSlugs={DOC_SLUGS} />
        <div className="jack-source">
          <p>Jack is built entirely with AgentDeck.</p>
          <Link href="/jack" className="cta-ghost">
            See how Jack is built
          </Link>
        </div>
        {children && <div className="jack-wiring">{children}</div>}
      </div>
    </section>
  )
}

/* --------------------------------------------------------------------- CTA */

export function FinalCTA() {
  return (
    <section className="final-cta">
      <h2 className="final-title">Build agentic software like software.</h2>
      <InstallLine />
      <div className="final-actions">
        <Link href="/meet-agentdeck/quickstart" className="cta-primary">
          Build your first Deck
        </Link>
        <Link href="/meet-agentdeck/overview" className="cta-ghost">
          Documentation
        </Link>
        <a href={JACK_SOURCE} target="_blank" rel="noreferrer" className="cta-ghost">
          GitHub
        </a>
      </div>
    </section>
  )
}
