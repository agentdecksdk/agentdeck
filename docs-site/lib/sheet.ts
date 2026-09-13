'use client'

import { useEffect } from 'react'

type SheetOptions = {
  /** Runs on every visible-viewport change, for a sheet with its own scroll anchor to keep. */
  onResize?: () => void
  /**
   * Stop the document scrolling behind the sheet.
   *
   * Only for a sheet that covers the whole viewport. The lock is `overflow: hidden` on the root,
   * which removes the scrollport `position: sticky` resolves against: any sticky chrome falls back
   * to its static offset, which at a scrolled position is far up the document. Under a full-screen
   * sheet that is invisible and reverts on close. Under a partial one it is the bar disappearing
   * and the article showing in its place, so a partial sheet takes `touch-action` (sheet.css) and
   * leaves the document alone.
   */
  lockPage?: boolean
}

/** Mounts a mobile sheet's viewport state: `--ad-kb`, the height the software keyboard takes from
 *  the layout viewport, and optionally the page lock.
 *
 *  A keyboard is a resize of the visible viewport, and `innerHeight` does not move when it opens:
 *  `visualViewport` is the only thing that reports it. Where that API is missing the variable stays
 *  0 and the sheet fills the layout viewport, which is the pre-keyboard behaviour rather than a
 *  broken one.
 */
export function useSheet(open: boolean, { onResize, lockPage }: SheetOptions = {}) {
  useEffect(() => {
    if (!open) return
    const root = document.documentElement
    const body = document.body

    // `overflow: hidden` is not enough on iOS. Focusing an input makes Safari scroll the document
    // to reveal it, and a `position: fixed` sheet is carried along with it: the chat ends up above
    // the visible area and has to be scrolled back to. Taking the body out of flow at its current
    // offset leaves nothing to scroll, so focus cannot move it. The offset is restored on close.
    const parked = window.scrollY
    if (lockPage) {
      root.classList.add('ad-sheet-locked')
      body.style.position = 'fixed'
      body.style.top = `-${parked}px`
      body.style.insetInline = '0'
    }

    const viewport = window.visualViewport
    const apply = () => {
      if (viewport) {
        // The visible box itself, not an inset to subtract from the bottom. A fixed element is
        // positioned against the *layout* viewport, and `offsetTop`/`height` say exactly where the
        // visible one sits inside it, keyboard and URL bar included. Deriving a keyboard height
        // instead over-lifts on iOS, because `innerHeight` counts the strip behind the URL bar as
        // well: that is the gap under the composer with the page showing through it.
        root.style.setProperty('--ad-vv-top', `${viewport.offsetTop}px`)
        root.style.setProperty('--ad-vv-h', `${viewport.height}px`)
        // Kept for one job only: whether the keyboard is covering the home indicator, which
        // decides the composer's safe-area padding. Over-reporting there is harmless, the padding
        // clamps.
        const covered = window.innerHeight - viewport.height - viewport.offsetTop
        root.style.setProperty('--ad-kb', `${Math.max(0, covered)}px`)
      }
      onResize?.()
    }

    apply()
    viewport?.addEventListener('resize', apply)
    viewport?.addEventListener('scroll', apply)
    return () => {
      viewport?.removeEventListener('resize', apply)
      viewport?.removeEventListener('scroll', apply)
      root.style.removeProperty('--ad-vv-top')
      root.style.removeProperty('--ad-vv-h')
      root.style.removeProperty('--ad-kb')
      if (!lockPage) return
      root.classList.remove('ad-sheet-locked')
      body.style.removeProperty('position')
      body.style.removeProperty('top')
      body.style.removeProperty('inset-inline')
      // The scroll position only exists in this closure now, so it is restored here or lost.
      window.scrollTo(0, parked)
    }
  }, [open, onResize, lockPage])
}
