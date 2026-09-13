import type { MetadataRoute } from 'next'
import { SITE } from '@/lib/site'

// `output: 'export'` builds every route ahead of time, and a route handler has to opt in
// explicitly or the build refuses rather than guessing. Nothing here reads a request.
export const dynamic = 'force-static'

/**
 * Allow everyone, and say so explicitly for the AI crawlers.
 *
 * A default-allow `robots.txt` would do the same thing silently, but several of these agents are
 * new enough that a missing rule gets read as an oversight by whoever audits it next  -  and the
 * whole point of `llms.txt` is that we *want* these readers. Naming them is the record of that.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: '*', allow: '/' },
      {
        userAgent: ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-Web',
                    'anthropic-ai', 'PerplexityBot', 'Perplexity-User', 'Google-Extended',
                    'Applebot-Extended', 'CCBot', 'cohere-ai'],
        allow: '/'
      }
    ],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE
  }
}
