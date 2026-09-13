'use client'

import { useEffect } from 'react'

/** Holds the document still under an open sheet.
 *
 *  Only for a sheet that covers the whole viewport. The body is parked out of flow at its current
 *  offset, which is stronger than `overflow: hidden` for the reason that matters on iOS: focusing
 *  an input makes Safari scroll the document to reveal it, and a `position: fixed` sheet is carried
 *  along and ends up above the visible area. With nothing to scroll, focus cannot move anything.
 *
 *  A partial sheet must not call this. The lock takes the scrollport `position: sticky` resolves
 *  against, so any chrome left visible falls to its static offset far up the document, and the
 *  article shows where the bar was.
 */
export function useLockedPage(locked: boolean) {
  useEffect(() => {
    if (!locked) return
    const root = document.documentElement
    const body = document.body
    const parked = window.scrollY

    root.classList.add('ad-sheet-locked')
    body.style.position = 'fixed'
    body.style.top = `-${parked}px`
    body.style.insetInline = '0'

    return () => {
      root.classList.remove('ad-sheet-locked')
      body.style.removeProperty('position')
      body.style.removeProperty('top')
      body.style.removeProperty('inset-inline')
      // The offset only exists in this closure now, so it is restored here or lost.
      window.scrollTo(0, parked)
    }
  }, [locked])
}
