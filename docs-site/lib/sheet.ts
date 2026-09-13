'use client'

import { useEffect } from 'react'

/** Mounts a mobile sheet's viewport state: the root class that locks the page behind it, and
 *  `--ad-kb`, the height the software keyboard takes from the layout viewport.
 *
 *  A keyboard is a resize of the visible viewport, and `innerHeight` does not move when it opens:
 *  `visualViewport` is the only thing that reports it. Where that API is missing the variable
 *  stays 0 and the sheet still fills the layout viewport, which is the pre-keyboard behaviour
 *  rather than a broken one.
 *
 *  `onViewportChange` runs on every resize, for a sheet that has its own anchor to keep.
 */
export function useSheet(open: boolean, onViewportChange?: () => void) {
  useEffect(() => {
    if (!open) return
    const root = document.documentElement
    root.classList.add('ad-sheet-open')

    const viewport = window.visualViewport
    const apply = () => {
      if (viewport) {
        const covered = window.innerHeight - viewport.height - viewport.offsetTop
        root.style.setProperty('--ad-kb', `${Math.max(0, covered)}px`)
      }
      onViewportChange?.()
    }

    apply()
    viewport?.addEventListener('resize', apply)
    viewport?.addEventListener('scroll', apply)
    return () => {
      viewport?.removeEventListener('resize', apply)
      viewport?.removeEventListener('scroll', apply)
      root.classList.remove('ad-sheet-open')
      root.style.removeProperty('--ad-kb')
    }
  }, [open, onViewportChange])
}
