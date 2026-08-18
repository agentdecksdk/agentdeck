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
        { name: 'search_examples', kind: 'tool' },
        { name: 'Jack', kind: 'agent' }
      ]
    }
  ]
}

const TREE_BUYS = [
  ['Observation', 'One ordered stream of what happened.'],
  ['Control', 'Execution can be paused, resumed or cancelled.'],
  ['Interaction', 'Branches can wait for external input.'],
  ['Durability', 'Execution does not have to live and die with the caller process.']
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
          You already know every node, because you just wrote them. AgentDeck does not replace
          your application with an opaque runtime model: it turns the execution of those pieces
          into something you can observe and control.
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

/* ------------------------------------------------------- 6. the system grows */

const GROWN_TREE: TreeNode = {
  name: 'Run',
  kind: 'run',
  children: [
    {
      name: 'answer',
      kind: 'workflow',
      children: [
        { name: 'search_docs', kind: 'tool' },
        {
          name: 'Researcher',
          kind: 'agent',
          children: [{ name: 'inspect_code', kind: 'tool' }]
        },
        { name: 'Jack', kind: 'agent' }
      ]
    }
  ]
}

const WAIT_SEMANTICS: [string, string][] = [
  ['ask', 'this branch needs external information'],
  ['approve', 'this branch needs an external decision'],
  ['wait', 'this branch is waiting for something']
]

/** The three ways a branch stops, named. Shown once, inside the interaction movement. */
export function WaitModel() {
  return (
    <dl className="semantics">
      {WAIT_SEMANTICS.map(([verb, meaning]) => (
        <div className="semantic" key={verb}>
          <dt>{verb}</dt>
          <dd>{meaning}</dd>
        </div>
      ))}
    </dl>
  )
}

/** One movement of the growing section: a name, a sentence, and the code that shows it. */
export function Movement({
  label,
  title,
  children
}: {
  label: string
  title: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div className="movement">
      <p className="movement-label">{label}</p>
      <p className="movement-title">{title}</p>
      {children}
    </div>
  )
}

export function Composition({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-wide">
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">05</span>
      </div>
      <div className="chapter-body">
        <p className="chapter-eyebrow">Composition</p>
        <h2 className="chapter-title">The system grows. The execution model doesn&apos;t.</h2>
        <p className="chapter-lead">
          Holding a developer conversation and investigating source code are different jobs, so
          they get different agents. That does not buy a second orchestration model: the
          researcher is one more node on the same tree, with a tool of its own underneath it.
        </p>
        <div className="chapter-grid">
          <div className="chapter-figure">
            <TreeFigure root={GROWN_TREE} />
          </div>
          <div className="chapter-code">{children}</div>
        </div>
      </div>
    </section>
  )
}

/* -------------------------------------------------------- 9. the whole deck */

export function WholeDeck({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-summary">
      <div className="chapter-spine" aria-hidden="true">
        <span className="chapter-step">06</span>
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

/* ------------------------------------------------------ 7. existing systems */

const SOURCES: [string, string, boolean][] = [
  ['AgentDeck native', 'agents, workflows and skills you declare here', true],
  ['LangGraph', 'your existing state graphs, checkpointed', true],
  ['OpenAI Agents', 'your existing SDK agents', true],
  ['MCP', 'tool servers, connected through the Deck', true],
  ['Your Python', 'any function that takes a Context', true],
  ['PydanticAI', 'designed, not yet built', false]
]

export function Interop() {
  return (
    <section className="chapter is-wide">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <p className="chapter-eyebrow">Existing systems</p>
        <h2 className="chapter-title">Bring what you already have.</h2>
        <p className="chapter-lead">
          Use AgentDeck&apos;s own primitives where they help, and wrap what already works where
          they do not. An Agent compiles to an SDK agent and a Workflow to a LangGraph graph, so
          if you need the engine object underneath, you keep access to it.
        </p>
        <div className="interop">
          <ul className="interop-sources">
            {SOURCES.map(([name, detail, shipped]) => (
              <li className={shipped ? 'interop-source' : 'interop-source is-proposed'} key={name}>
                <span className="interop-marker" aria-hidden="true">
                  {shipped ? '◆' : '◇'}
                </span>
                <span className="interop-name">{name}</span>
                <span className="interop-detail">{detail}</span>
              </li>
            ))}
          </ul>
          <div className="interop-sink" aria-hidden="true">
            <span className="interop-target">Deck</span>
            <span className="interop-stem" />
            <span className="interop-target is-run">Execution</span>
          </div>
        </div>
      </div>
    </section>
  )
}

/* ---------------------------------------------------------- 8. the boundary */

const BOUNDARY: [string, string][] = [
  ['application logic', 'execution'],
  ['agents', 'lifecycle'],
  ['tools', 'control'],
  ['workflows', 'interruptions'],
  ['business state', 'durability'],
  ['integrations', 'recovery']
]

export function Boundary() {
  return (
    <section className="chapter is-wide">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <p className="chapter-eyebrow">The line</p>
        <h2 className="chapter-title">You build the behavior. AgentDeck manages the machinery.</h2>
        <table className="boundary">
          <thead>
            <tr>
              <th scope="col">You</th>
              <th scope="col">AgentDeck</th>
            </tr>
          </thead>
          <tbody>
            {BOUNDARY.map(([yours, ours]) => (
              <tr key={yours}>
                <td>{yours}</td>
                <td>{ours}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/* ------------------------------------------------------------- 12. real Jack */

export function MeetJack({ children }: { children?: React.ReactNode }) {
  return (
    <section className="chapter is-wide is-live-section">
      <div className="chapter-spine" aria-hidden="true" />
      <div className="chapter-body">
        <p className="chapter-eyebrow">Jack, running</p>
        <h2 className="chapter-title">Anything you want to ask?</h2>
        <p className="chapter-lead">
          This is the same Jack we built above. Ask about AgentDeck, architecture, integrations,
          or code you are working with. The tree beside him is his actual run: every node is an
          event the runtime emitted, in the order it emitted it.
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
