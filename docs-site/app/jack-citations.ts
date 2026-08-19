/**
 * Jack cites documentation pages by slug at the end of an answer, but not in one fixed shape:
 * a bare slug, a backticked one, `[slug]`, or a markdown link -- sometimes pointed at the wrong
 * host (`https://agentdeck.com/...`). This turns every form that names a real page into a same
 * -site link, and leaves anything that doesn't match a real slug exactly as plain text.
 *
 * A remark plugin (operating on the parsed markdown tree) rather than a regex over the raw
 * string: it only ever touches text, inline-code and link nodes, so it can never reach inside a
 * fenced code block.
 */

// Structural rather than importing mdast's types: this file has no other reason to depend on it.
interface MdNode {
  type: string
  value?: string
  url?: string
  children?: MdNode[]
}

const AGENTDECK_HOST = /^https?:\/\/agentdeck\.com/i
const EXTERNAL_URL = /^[a-z][a-z0-9+.-]*:\/\//i

// A citation slug: one or two kebab-case path segments, e.g. "jack" or "build-your-deck/deck".
// Matched either bracketed (`[slug]`, not already a markdown link) or bare with a slash (so a
// single common word like "jack" or "index" in ordinary prose is never mistaken for a citation).
const CANDIDATE = /\[([a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)?)\](?!\()|\b([a-z][a-z0-9-]*\/[a-z][a-z0-9-]*)\b/g

function trimSlashes(value: string): string {
  return value.replace(/^\/+/, '').replace(/\/+$/, '')
}

/** What a link's href resolves to. `citationShaped` is false only for a link to some other real
 * external site, which is left alone entirely. */
function resolveHref(url: string): { citationShaped: boolean; slug: string | null } {
  if (AGENTDECK_HOST.test(url)) return { citationShaped: true, slug: trimSlashes(url.replace(AGENTDECK_HOST, '')) }
  if (EXTERNAL_URL.test(url)) return { citationShaped: false, slug: null }
  return { citationShaped: true, slug: trimSlashes(url) }
}

function linkNode(slug: string, children: MdNode[]): MdNode {
  return { type: 'link', url: `/${slug}`, children }
}

function splitText(node: MdNode, slugs: Set<string>): MdNode[] {
  const value = node.value ?? ''
  const parts: MdNode[] = []
  let last = 0
  for (const match of value.matchAll(CANDIDATE)) {
    const candidate = match[1] ?? match[2]
    if (!slugs.has(candidate)) continue
    const start = match.index
    if (start > last) parts.push({ type: 'text', value: value.slice(last, start) })
    parts.push(linkNode(candidate, [{ type: 'text', value: candidate }]))
    last = start + match[0].length
  }
  if (parts.length === 0) return [node]
  if (last < value.length) parts.push({ type: 'text', value: value.slice(last) })
  return parts
}

function transform(node: MdNode, slugs: Set<string>): void {
  if (!node.children) return
  const next: MdNode[] = []
  for (const child of node.children) {
    if (child.type === 'inlineCode' && child.value && slugs.has(child.value)) {
      next.push(linkNode(child.value, [child]))
    } else if (child.type === 'link' && typeof child.url === 'string') {
      const { citationShaped, slug } = resolveHref(child.url)
      if (!citationShaped) {
        next.push(child)
      } else if (slug && slugs.has(slug)) {
        child.url = `/${slug}`
        next.push(child)
      } else if (child.children) {
        // A citation-shaped link to a page that doesn't exist: keep the words, drop the dead link.
        next.push(...child.children)
      }
    } else if (child.type === 'text') {
      next.push(...splitText(child, slugs))
    } else {
      transform(child, slugs)
      next.push(child)
    }
  }
  node.children = next
}

/** A unified/remark attacher: `[jackCitationsPlugin, slugs]` in a `remarkPlugins` list. */
export function jackCitationsPlugin(slugs: Set<string>) {
  return (tree: MdNode) => {
    transform(tree, slugs)
  }
}
