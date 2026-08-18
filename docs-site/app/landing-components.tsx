import React from 'react'
import Link from 'next/link'

import { DeckFigure, TreeFigure, type DeckGroup, type TreeNode } from './deck-figure'
import { JackLive } from './jack-live'
import { InstallLine } from './install-line'
import { Hero } from './hero'

/**
 * The landing page, as one story rather than a feature list.
 *
 * Every section builds the same application: Jack, the developer agent this site actually runs.
 * The figures are two components (`DeckFigure`, `TreeFigure`) fed different rows, so a section
 * adds to a picture the reader already recognises instead of introducing a new one.
 *
 * Every code block carries the release it belongs to. `shipped` is in the version named on the
 * chip and runs today; `proposed` is designed and not built yet. A landing page that mixes the
 * two without saying so is a landing page whose examples fail on paste.
 */

export { Hero }

const RELEASE = '4.0.0'
const REPO = 'https://github.com/agentdecksdk/agentdeck'
const JACK_SOURCE = `${REPO}/tree/dev/examples/jack`

type Status = 'shipped' | 'proposed'

/**
 * What release a block belongs to, on the block. `shipped` runs today; `proposed` is designed
 * and not built yet. Sections carrying both need this per block, not per section.
 */
export function Snippet({ status, label, children }: { status: Status; label?: string; children?: React.ReactNode }) {
  const shipped = status === 'shipped'
  return (
    <div className={`snippet is-${status}`}>
      <p className="snippet-head">
        <span className={`status-chip is-${status}`}>
          <span aria-hidden="true">{shipped ? '◆' : '◇'}</span>
          {shipped ? `v${RELEASE}` : 'proposed'}
        </span>
        {label && <span className="snippet-label">{label}</span>}
      </p>
      {children}
    </div>
  )
}

/**
 * One beat of the story: a number on the spine, a heading, prose, the code, and the figure the
 * code just changed.
 */
function Chapter({
  step,
  eyebrow,
  title,
  lead,
  note,
  figure,
  children,
  wide
}: {
  step: string
  eyebrow: string
  title: React.ReactNode
  lead: React.ReactNode
  note?: React.ReactNode
  figure?: React.ReactNode
  children?: React.ReactNode
  wide?: boolean
}) {
  return (
    <section className={wide ? 'chapter is-wide' : 'chapter'}>
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">{step}</span>
      </div>
      <div className="chapter-body">
        <p className="chapter-eyebrow">{eyebrow}</p>
        <h2 className="chapter-title">{title}</h2>
        <p className="chapter-lead">{lead}</p>
        {note && <p className="chapter-note">{note}</p>}
        <div className="chapter-grid">
          {children && <div className="chapter-code">{children}</div>}
          {figure && <div className="chapter-figure">{figure}</div>}
        </div>
      </div>
    </section>
  )
}

/* ------------------------------------------------- 2. compose the first deck */

const AGENTS_ONLY: DeckGroup[] = [{ label: 'Agents', rows: [{ name: 'Jack', added: true }] }]

export function Compose({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="01"
      eyebrow="Compose"
      title="Let's build Jack."
      lead={
        <>
          Jack is a developer agent who helps people build with AgentDeck. He is also the agent
          answering questions at the bottom of this page, so everything below is a real system
          rather than an illustration of one.
        </>
      }
      figure={<DeckFigure groups={AGENTS_ONLY} caption="A Deck is where an agentic application comes together." />}
    >
      {children}
    </Chapter>
  )
}

/* --------------------------------------------------------- 3. give him tools */

const WITH_TOOLS: DeckGroup[] = [
  { label: 'Agents', rows: [{ name: 'Jack' }] },
  {
    label: 'Tools',
    rows: [
      { name: 'search_docs', added: true },
      { name: 'read_doc', added: true },
      { name: 'read_changelog', added: true }
    ]
  }
]

export function Capabilities({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="02"
      eyebrow="Capabilities"
      title="Jack needs to know things."
      lead={
        <>
          Three functions over one context: find a page, read a page, read the release history. A
          tool that takes a <code>Context</code> stays an ordinary function, because the model is
          offered only the arguments it can actually choose.
        </>
      }
      figure={<DeckFigure groups={WITH_TOOLS} caption="Tools do work. Agents make decisions." />}
    >
      {children}
    </Chapter>
  )
}

/* ------------------------------------------------------------- 4. a workflow */

const WITH_WORKFLOW: DeckGroup[] = [
  { label: 'Agents', rows: [{ name: 'Jack' }] },
  {
    label: 'Tools',
    rows: [{ name: 'search_docs' }, { name: 'read_doc' }, { name: 'read_changelog' }]
  },
  { label: 'Workflows', rows: [{ name: 'answer', added: true }] }
]

export function Process({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="03"
      eyebrow="Process"
      title="Not every step should be a decision."
      lead={
        <>
          Jack should always read the docs before he answers. That is a process, not a judgement
          call, so it belongs in code: functions, <code>await</code>, values, <code>if</code>,{' '}
          <code>return</code>. No DSL to learn, and nothing a model can decide to skip.
        </>
      }
      note={
        <>
          ◇ proposed means designed and not yet in v{RELEASE}. Workflows today are LangGraph
          graphs: see <Link href="/build-your-deck/workflows">Workflows</Link> for what runs now.
        </>
      }
      figure={<DeckFigure groups={WITH_WORKFLOW} caption="Agents make decisions. Workflows manage process." />}
    >
      {children}
    </Chapter>
  )
}

/* -------------------------------------------------------- 5. the tree itself */

const FIRST_TREE: TreeNode = {
  name: 'Run',
  kind: 'run',
  children: [
    {
      name: 'answer',
      kind: 'workflow',
      children: [
        { name: 'search_docs', kind: 'tool' },
        { name: 'read_doc', kind: 'tool' },
        { name: 'Jack', kind: 'agent' }
      ]
    }
  ]
}

const TREE_BUYS = [
  ['Observation', 'one ordered event log per run'],
  ['Lifecycle', 'pause, resume, cancel'],
  ['Interaction', 'a branch can wait for a person'],
  ['Durability', 'a run outlives the process that started it']
]

export function ExecutionTree() {
  return (
    <section className="chapter is-pivot">
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">04</span>
      </div>
      <div className="chapter-body">
        <p className="chapter-eyebrow">Runtime</p>
        <h2 className="chapter-title">Everything becomes one execution tree.</h2>
        <p className="chapter-lead">
          You already know every node here, because you just watched us write them. That is the
          whole idea: one execution model, and nothing in it you did not put there.
        </p>
        <div className="chapter-grid">
          <div className="chapter-figure">
            <TreeFigure root={FIRST_TREE} />
          </div>
          <dl className="tree-buys">
            {TREE_BUYS.map(([term, detail]) => (
              <div className="tree-buy" key={term}>
                <dt>{term}</dt>
                <dd>{detail}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  )
}

/* ------------------------------------------------------- 6. a second agent */

const WITH_RESEARCHER: DeckGroup[] = [
  { label: 'Agents', rows: [{ name: 'Jack' }, { name: 'Researcher', added: true }] },
  {
    label: 'Tools',
    rows: [{ name: 'search_docs' }, { name: 'read_doc' }, { name: 'read_changelog' }, { name: 'inspect_code', added: true }]
  },
  { label: 'Workflows', rows: [{ name: 'answer' }] }
]

export function SecondAgent({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="05"
      eyebrow="Delegation"
      title="Give Jack a researcher."
      lead={
        <>
          Reading the source is a different job from holding a conversation, and it is the job
          that fills a context window. So it gets its own agent: Jack keeps talking to you while
          the Researcher goes digging.
        </>
      }
      figure={<DeckFigure groups={WITH_RESEARCHER} caption="A second agent is a second node, not a second runtime." />}
    >
      {children}
    </Chapter>
  )
}

/* ------------------------------------------------------ 7. waiting on a human */

const WAIT_SEMANTICS = [
  ['ask', 'this branch needs information from a person'],
  ['approve', 'this branch needs a decision from a person'],
  ['wait', 'this branch is waiting on something else']
]

export function Interaction({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="06"
      eyebrow="Interaction"
      title="When Jack needs you."
      lead={
        <>
          Sometimes the answer depends on code only you have. Waiting is a property of the branch
          that waits, not of the whole run: everything else keeps going.
        </>
      }
      figure={
        <dl className="semantics">
          {WAIT_SEMANTICS.map(([verb, meaning]) => (
            <div className="semantic" key={verb}>
              <dt>{verb}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
      }
    >
      {children}
    </Chapter>
  )
}

/* ------------------------------------------------------------- 8. control */

export function Control({ children }: { children?: React.ReactNode }) {
  return (
    <Chapter
      step="07"
      eyebrow="Control"
      title="Execution you can steer."
      lead={
        <>
          A Run is the root execution, and control belongs to the handle rather than to a separate
          lifecycle API. Pause it, resume it, cancel it, answer it. Nested invocations are the same
          handle one level down.
        </>
      }
    >
      {children}
    </Chapter>
  )
}

/* -------------------------------------------------------- 9. the whole deck */

export function WholeDeck({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-summary">
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">08</span>
      </div>
      <div className="chapter-body">
        <p className="chapter-eyebrow">Assembled</p>
        <h2 className="chapter-title">One Deck. One execution model.</h2>
        <p className="chapter-lead">
          Everything above, in one object. Tools belong to the agents that use them, and the Deck
          holds the roots you can start a run on.
        </p>
        <div className="chapter-grid">
          <div className="chapter-code">{children}</div>
          <div className="chapter-figure">
            <svg className="assembly" viewBox="0 0 340 250" role="img" aria-label="Agents, workflows and tools converge into one Deck, which runs as one Run">
              <g className="assembly-labels">
                <text x="52" y="24">Agents</text>
                <text x="170" y="24">Workflows</text>
                <text x="288" y="24">Tools</text>
              </g>
              <g className="assembly-lines">
                <path d="M52 40 V64 H288 V40" />
                <path d="M170 40 V64" />
                <path d="M170 64 V96" />
                <path d="M170 150 V182" />
              </g>
              <g className="assembly-boxes">
                <rect className="is-deck" x="110" y="96" width="120" height="54" />
                <rect className="is-run" x="110" y="182" width="120" height="54" />
              </g>
              <text className="assembly-name is-deck" x="170" y="129">DECK</text>
              <text className="assembly-name is-run" x="170" y="215">RUN</text>
            </svg>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ------------------------------------------------------------- 12. real Jack */

export function MeetJack({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-wide is-live-section">
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">09</span>
      </div>
      <div className="chapter-body">
        <p className="chapter-eyebrow">Jack, running</p>
        <h2 className="chapter-title">Anything you want to ask?</h2>
        <p className="chapter-lead">
          This is the agent we built above, answering from the documentation on this site. The tree
          on the right is his actual run: every node is an event the runtime emitted, in the order
          it emitted it.
        </p>
        <JackLive />
        <div className="jack-source">
          <p>Jack is built entirely with AgentDeck.</p>
          <a href={JACK_SOURCE} target="_blank" rel="noreferrer" className="cta-ghost">
            View Jack&apos;s source
          </a>
        </div>
        {children && <div className="jack-wiring">{children}</div>}
      </div>
    </section>
  )
}

/* ------------------------------------------------------------------ 13. CTA */

export function FinalCTA() {
  return (
    <section className="final-cta">
      <h2 className="final-title">Build agentic software like software.</h2>
      <InstallLine />
      <div className="final-actions">
        <Link href="/meet-agentdeck/quickstart" className="cta-primary">
          Build your first Deck
        </Link>
        <a href={JACK_SOURCE} target="_blank" rel="noreferrer" className="cta-ghost">
          View Jack&apos;s source
        </a>
      </div>
    </section>
  )
}
