/**
 * One glyph per kind, drawn in the page's own geometry: thin structural lines, nodes, and
 * the mark's 45-degree cut corner.
 *
 * Its own module, with no `'use client'`, because both the hero (a client component) and the
 * Deck figure (a server one) draw them. Exported from the client module, these arrived on the
 * server as client references and rendered as empty boxes.
 */

import React from 'react'

export type ModuleKey = 'agent' | 'tool' | 'workflow' | 'skill'

export const ICONS: Record<ModuleKey, React.ReactNode> = {
  // A node that decides: a hexagon holding a single point.
  agent: (
    <>
      <path d="M8 2 13.2 5 13.2 11 8 14 2.8 11 2.8 5Z" />
      <circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none" />
    </>
  ),
  // A thing that does work.
  tool: (
    <>
      <circle cx="5.6" cy="10.4" r="3.2" />
      <path d="M8 8.1 13.2 2.9" />
    </>
  ),
  // Process: one step in, two steps out.
  workflow: (
    <>
      <path d="M4.6 8H8m0 0V4.6h3.4M8 8v3.4h3.4" />
      <circle cx="3" cy="8" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="13" cy="4.6" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="13" cy="11.4" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  // Something written down, on the mark's own cut-corner card.
  skill: (
    <>
      <path d="M3.4 2.2h6.7l2.5 2.5v9.1H3.4Z" />
      <path d="M5.9 7.6h4.2M5.9 10.3h3" />
    </>
  ),
};
