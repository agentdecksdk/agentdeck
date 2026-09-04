import { createMDX } from 'fumadocs-mdx/next'

const withMDX = createMDX()
const isGitHubPages = process.env.GITHUB_ACTIONS === 'true'

export default withMDX({
  output: 'export',
  trailingSlash: true,
  basePath: isGitHubPages ? '/agentdeck' : '',
  assetPrefix: isGitHubPages ? '/agentdeck/' : '',
  images: {
    unoptimized: true
  }
})
