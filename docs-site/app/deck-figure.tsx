import React from 'react'

import { ICONS, type ModuleKey } from './kind-icons'

/**
 * The two figures the landing page reuses instead of drawing a new diagram per section.
 *
 * `DeckFigure` is Jack's deck as it stands at that point in the page, and `TreeFigure` is the
 * same catalog seen from the runtime side. Both take their rows as data so a section adds to
 * what the reader already recognises rather than replacing it: the `added` flag is what the
 * section just introduced, and it is the only thing that draws the eye.
 */

export interface DeckRow {
  name: string
  /** Introduced by the section drawing this figure. Everything else is already familiar. */
  added?: boolean
}

/** The group's label, mapped to the glyph the hero gave that kind. One vocabulary, two figures. */
const KIND_OF: Record<string, ModuleKey> = {
  Agents: 'agent',
  Tools: 'tool',
  Workflows: 'workflow',
  Skills: 'skill'
}

function KindIcon({ group }: { group: string }) {
  const kind = KIND_OF[group]
  if (!kind) return null
  return (
    <span className="deck-row-icon" aria-hidden="true">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
        {ICONS[kind]}
      </svg>
    </span>
  )
}

export interface DeckGroup {
  label: string
  rows: DeckRow[]
}

export function DeckFigure({ groups, caption }: { groups: DeckGroup[]; caption?: string }) {
  return (
    <figure className="deck-figure">
      <div className="deck-figure-head">Jack&apos;s Deck</div>
      <div className="deck-figure-body">
        {groups.map(group => (
          <div className="deck-group" key={group.label}>
            <div className="deck-group-label">{group.label}</div>
            <ul className="deck-rows">
              {group.rows.map(row => (
                <li className={row.added ? 'deck-row is-added' : 'deck-row'} key={row.name}>
                  <KindIcon group={group.label} />
                  <span className="deck-row-name">{row.name}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {caption && <figcaption className="deck-figure-caption">{caption}</figcaption>}
    </figure>
  )
}

export interface TreeNode {
  name: string
  /** What the runtime calls it: shown as the node's kind, never invented. */
  kind?: 'agent' | 'workflow' | 'tool' | 'run'
  detail?: string
  state?: 'done' | 'running' | 'waiting' | 'failed'
  children?: TreeNode[]
}

const STATE_MARK: Record<string, string> = {
  done: '✓',
  running: '●',
  waiting: '◆',
  failed: '×'
}

function TreeRow({ node, prefix, last, depth }: { node: TreeNode; prefix: string; last: boolean; depth: number }) {
  const branch = depth === 0 ? '' : `${prefix}${last ? '└─ ' : '├─ '}`
  const childPrefix = depth === 0 ? '' : `${prefix}${last ? '   ' : '│  '}`
  return (
    <>
      <li className={`tree-row${node.state ? ` is-${node.state}` : ''}`}>
        <span className="tree-branch" aria-hidden="true">
          {branch}
        </span>
        <span className={`tree-name${node.kind ? ` is-${node.kind}` : ''}`}>{node.name}</span>
        {node.detail && <span className="tree-detail">{node.detail}</span>}
        {node.state && (
          <span className="tree-state" aria-label={node.state}>
            {STATE_MARK[node.state]}
          </span>
        )}
      </li>
      {node.children?.map((child, at) => (
        <TreeRow
          key={`${child.name}-${at}`}
          node={child}
          prefix={childPrefix}
          last={at === node.children!.length - 1}
          depth={depth + 1}
        />
      ))}
    </>
  )
}

export function TreeFigure({ root, caption, live }: { root: TreeNode; caption?: string; live?: boolean }) {
  return (
    <figure className={live ? 'tree-figure is-live' : 'tree-figure'}>
      <ul className="tree-rows">
        <TreeRow node={root} prefix="" last depth={0} />
      </ul>
      {caption && <figcaption className="tree-figure-caption">{caption}</figcaption>}
    </figure>
  )
}
