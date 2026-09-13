import Link from 'next/link'

const REPORT =
  'https://github.com/agentdecksdk/agentdeck/issues/new?labels=documentation,broken-link' +
  '&title=' + encodeURIComponent('Broken documentation link')

export default function NotFound() {
  return (
    <main className="not-found">
      <h1>404: Page not found</h1>
      <p>The documentation page you requested does not exist or may have moved.</p>
      <p>
        <Link href="/">Back to the documentation</Link>
        {' · '}
        <a href={REPORT} target="_blank" rel="noreferrer">Report a broken documentation link</a>
      </p>
    </main>
  )
}
