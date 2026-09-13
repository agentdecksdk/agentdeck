'use client'

import { useEffect } from 'react'

/** Holds the document still under a sheet that covers the whole screen.
 *
 *  The body is parked out of flow rather than given `overflow: hidden`, because iOS scrolls the
 *  document to reveal a focused input and carries a `position: fixed` sheet with it. A sheet that
 *  leaves chrome visible must not call this: the lock takes the scrollport `position: sticky`
 *  resolves against, and the bar falls to its static offset far up the document.
 */
export function useLockedPage(locked: boolean) {
  useEffect(() => {
    if (!locked) return
    const body = document.body
    const parked = window.scrollY

    body.style.position = 'fixed'
    body.style.top = `-${parked}px`
    body.style.insetInline = '0'

    return () => {
      body.style.removeProperty('position')
      body.style.removeProperty('top')
      body.style.removeProperty('inset-inline')
      window.scrollTo(0, parked)
    }
  }, [locked])
}
