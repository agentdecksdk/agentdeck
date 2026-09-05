import { MarkdownCopyButton, ViewOptionsPopover } from 'fumadocs-ui/layouts/notebook/page'

const REPO_CONTENT_URL = 'https://github.com/agentdecksdk/agentdeck/blob/main/docs-site/content'

// `markdownUrl` points at `public/llms/<slug>.md`, written by
// `scripts/generate_docs_reference.py` at build time  -  there is no per-request route in a
// statically exported site to derive it from `path` on demand.
export function PageActions({ url, path }: { url: string; path: string }) {
  const markdownUrl = `/llms${url}.md`

  return (
    <div className="flex items-center gap-2 mb-4">
      <MarkdownCopyButton markdownUrl={markdownUrl} />
      <ViewOptionsPopover markdownUrl={markdownUrl} githubUrl={`${REPO_CONTENT_URL}/${path}`} />
    </div>
  )
}
