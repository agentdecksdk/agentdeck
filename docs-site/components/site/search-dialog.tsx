'use client'

import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { addBasePath } from 'next/dist/client/add-base-path'
import type { ReactSortedResult } from 'fumadocs-core/search'
import {
  SearchDialog,
  SearchDialogContent,
  SearchDialogHeader,
  SearchDialogIcon,
  SearchDialogInput,
  SearchDialogList,
  SearchDialogOverlay,
  type SharedProps
} from 'fumadocs-ui/components/dialog/search'

/**
 * Search, over the Pagefind index the `postbuild` script writes.
 *
 * Fumadocs' default dialog talks to Orama, which under `output: 'export'` means shipping the
 * whole index to the browser. Pagefind already indexes the built `.html` and CI gates on it, so
 * only the dialog is ours: `RootProvider`'s `search.SearchDialog` is the seam for that.
 */

interface PagefindSubResult {
  title: string
  url: string
  excerpt: string
}

interface PagefindData {
  url: string
  excerpt: string
  meta: { title?: string }
  sub_results?: PagefindSubResult[]
}

interface Pagefind {
  options: (config: { baseUrl: string }) => Promise<void>
  debouncedSearch: (query: string) => Promise<{ results: { id: string; data: () => Promise<PagefindData> }[] } | null>
}

let pagefind: Promise<Pagefind> | undefined
let attempt = 0

function loadPagefind(): Promise<Pagefind> {
  // Built by `postbuild`, so it exists only in the export and must not be resolved at build time.
  // A failed specifier stays failed in the document's module map, so a retry needs a URL the map
  // has not seen: without the counter one lost fetch leaves search dead until the next page load.
  const url = addBasePath(`/_pagefind/pagefind.js${attempt ? `?retry=${attempt}` : ''}`)
  pagefind ??= (import(/* webpackIgnore: true */ url) as Promise<Pagefind>)
    // `baseUrl: '/'` keeps results as site-relative paths; `next/link` adds the base path itself.
    .then(async module => {
      await module.options({ baseUrl: '/' })
      return module
    })
    .catch(failure => {
      pagefind = undefined
      attempt += 1
      throw failure
    })
  return pagefind
}

/** Pagefind marks matched terms with `<mark>`; rendering the nodes avoids handing it raw HTML. */
function excerpt(html: string): ReactNode {
  return html.split(/<mark>|<\/mark>/).map((part, index) => (
    <Fragment key={index}>{index % 2 === 0 ? part : <mark>{part}</mark>}</Fragment>
  ))
}

async function results(query: string): Promise<ReactSortedResult[]> {
  const response = await (await loadPagefind()).debouncedSearch(query)
  if (!response) return []

  const sorted: ReactSortedResult[] = []
  for (const result of response.results.slice(0, 8)) {
    const data = await result.data()
    sorted.push({ id: result.id, url: data.url, type: 'page', content: data.meta.title ?? data.url })
    const sections = data.sub_results?.length
      ? data.sub_results
      : [{ title: '', url: data.url, excerpt: data.excerpt }]
    for (const section of sections) {
      if (section.title) sorted.push({ id: `${section.url}#h`, url: section.url, type: 'heading', content: section.title })
      sorted.push({ id: section.url, url: section.url, type: 'text', content: excerpt(section.excerpt) })
    }
  }
  return sorted
}

export default function PagefindSearchDialog(props: SharedProps) {
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<ReactSortedResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  // `npm run dev` never runs `postbuild`, so `/_pagefind/pagefind.js` 404s there: distinct from a
  // real query with zero matches, and worth telling the developer apart from "no results".
  const [noIndex, setNoIndex] = useState(false)

  useEffect(() => {
    if (!query) {
      setItems(null)
      return
    }
    let live = true
    setLoading(true)
    results(query)
      .then(found => {
        if (!live) return
        setNoIndex(false)
        setItems(found)
      })
      .catch(() => {
        if (!live) return
        setNoIndex(true)
        setItems([])
      })
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [query])

  return (
    <SearchDialog {...props} search={query} onSearchChange={setQuery} isLoading={loading}>
      <SearchDialogOverlay />
      <SearchDialogContent>
        <SearchDialogHeader>
          <SearchDialogIcon />
          <SearchDialogInput placeholder="Search docs" />
        </SearchDialogHeader>
        <SearchDialogList
          items={items}
          Empty={() =>
            noIndex ? (
              <div className="py-12 text-center text-sm text-fd-muted-foreground">
                No search index in dev. Run `npm run build` to generate one.
              </div>
            ) : (
              <div className="py-12 text-center text-sm text-fd-muted-foreground">No results found</div>
            )
          }
        />
      </SearchDialogContent>
    </SearchDialog>
  )
}
