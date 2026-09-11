'use client'

import {
  Suspense,
  createContext,
  use,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode
} from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface HotKey {
  /** Matches the keydown that opens the dialog. */
  key: string | ((e: KeyboardEvent) => boolean)
  /** What the trigger shows in its `kbd` chip. */
  display: ReactNode
}

interface SearchContextValue {
  enabled: boolean
  open: boolean
  hotKey: HotKey[]
  setOpenSearch: (open: boolean) => void
}

const SearchContext = createContext<SearchContextValue>({
  enabled: false,
  open: false,
  hotKey: [],
  setOpenSearch: () => undefined
})

export function useSearchContext() {
  return use(SearchContext)
}

/** Windows and Linux label the modifier "Ctrl"; everything else gets the glyph.
 *
 *  Resolved after mount rather than during SSR, because the same HTML is served to every visitor.
 *  The trigger reserves the wider of the two boxes so the swap changes the glyph, not the layout.
 */
function MetaOrControl() {
  const [key, setKey] = useState('⌘')
  useEffect(() => {
    if (/Windows|Linux/i.test(window.navigator.userAgent)) setKey('Ctrl')
  }, [])
  return key
}

const HOT_KEYS: HotKey[] = [
  { key: (e) => e.metaKey || e.ctrlKey, display: <MetaOrControl /> },
  { key: 'k', display: 'K' }
]

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Holds whether the search dialog is open, and mounts it.
 *
 *  The dialog is only rendered once it has been opened, so the Pagefind bundle is not fetched by a
 *  reader who never searches.
 */
export function SearchProvider({
  dialog: Dialog,
  children
}: {
  dialog: ComponentType<DialogProps>
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (!HOT_KEYS.every((k) => (typeof k.key === 'string' ? e.key === k.key : k.key(e)))) return
      e.preventDefault()
      setMounted(true)
      setOpen((v) => !v)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const value = useMemo<SearchContextValue>(
    () => ({
      enabled: true,
      open,
      hotKey: HOT_KEYS,
      setOpenSearch: (next: boolean) => {
        if (next) setMounted(true)
        setOpen(next)
      }
    }),
    [open]
  )

  return (
    <SearchContext value={value}>
      <Suspense fallback={null}>{mounted && <Dialog open={open} onOpenChange={setOpen} />}</Suspense>
      {children}
    </SearchContext>
  )
}

/** The icon-only trigger the bar carries below the search field's breakpoint. */
export function SearchTrigger({ className }: { className?: string }) {
  const { enabled, setOpenSearch } = useSearchContext()
  if (!enabled) return null
  return (
    <button
      type="button"
      data-search=""
      aria-label="Open Search"
      onClick={() => setOpenSearch(true)}
      className={cn(
        'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors duration-100',
        'disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2',
        'focus-visible:ring-fd-ring hover:bg-fd-accent hover:text-fd-accent-foreground [&_svg]:size-4.5',
        className
      )}
    >
      <Search />
    </button>
  )
}
