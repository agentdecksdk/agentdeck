import { NotFoundPage } from 'nextra-theme-docs'

export default function NotFound() {
  return (
    <NotFoundPage content="Report a broken documentation link" labels="documentation,broken-link">
      <h1>404: Page not found</h1>
      <p>The documentation page you requested does not exist or may have moved.</p>
    </NotFoundPage>
  )
}
