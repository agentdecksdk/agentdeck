'use client'

import type { ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { ThemeProvider, useTheme } from 'next-themes'
import { NextProvider } from 'fumadocs-core/framework/next'
import { DirectionProvider } from '@radix-ui/react-direction'
import { useEffect } from 'react'
import PagefindSearchDialog from '@/components/site/search-dialog'
import { SearchProvider } from '@/components/site/search-context'

/** `d` toggles the theme, unless the reader is typing into something. */
function ThemeHotKey() {
  const { setTheme, resolvedTheme } = useTheme()

  useEffect(() => {
    function isTyping(target: EventTarget | null) {
      if (!(target instanceof HTMLElement)) return false
      if (target.isContentEditable) return true
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return true
      return target.closest('[role="dialog"]') !== null
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented || e.isComposing) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key.toLowerCase() !== 'd' || isTyping(e.target)) return
      e.preventDefault()
      const next = resolvedTheme === 'dark' ? 'light' : 'dark'
      if (document.startViewTransition) {
        document.startViewTransition(() => flushSync(() => setTheme(next)))
      } else {
        setTheme(next)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [resolvedTheme, setTheme])

  return null
}

/** What `RootProvider` used to wrap the site in: the router bindings fumadocs-core needs, writing
 *  direction for the Radix primitives, the theme, and the search dialog's open state.
 */
export function SiteProviders({ children }: { children: ReactNode }) {
  return (
    <NextProvider>
      <DirectionProvider dir="ltr">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <ThemeHotKey />
          <SearchProvider dialog={PagefindSearchDialog}>{children}</SearchProvider>
        </ThemeProvider>
      </DirectionProvider>
    </NextProvider>
  )
}
