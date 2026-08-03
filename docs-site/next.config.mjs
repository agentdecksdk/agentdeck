import nextra from 'nextra'

const withNextra = nextra({
  contentDirBasePath: '/docs'
})

const isGitHubPages = process.env.GITHUB_ACTIONS === 'true'

export default withNextra({
  output: 'export',
  trailingSlash: true,
  basePath: isGitHubPages ? '/agentdeck' : '',
  assetPrefix: isGitHubPages ? '/agentdeck/' : '',
  images: {
    unoptimized: true
  }
})
