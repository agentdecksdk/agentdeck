/**
 * One reader for Jack's event stream, shared by the docs panel and the landing page.
 *
 * `POST /ask` streams the run's canonical events as SSE frames, one `Event` per frame, dumped as
 * it was written. There is no translation layer: what a consumer switches on here is the same
 * `event.kind` a Python process reading the run back would switch on.
 */

// Build-time, not runtime: the bundle is static, so this is baked in when the site is built.
// Defaults to localhost so `npm run dev` works against a locally running backend with no setup.
export const JACK_API = process.env.NEXT_PUBLIC_AGENTDECK_API_URL || 'http://localhost:8100'

/** The envelope every event carries, narrowed to the fields either surface reads. */
export interface JackEvent {
  kind: string
  seq: number
  run_id: string
  origin: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: any
}

export interface JackQuestion {
  question: string
  page?: string | null
  selection?: string | null
  session_id?: string | null
}

/** Thrown when the backend answers, but with a refusal or an error rather than a stream. */
export class JackUnavailable extends Error {}

/**
 * Ask Jack, yielding each canonical event as it arrives.
 *
 * A generator rather than a callback because both callers are doing the same thing with the
 * result and differ only in which kinds they care about: the panel renders `text.delta`, the
 * landing page also builds a tree out of `run.started` and `tool.call.started`.
 */
export async function* askJack(asked: JackQuestion, signal?: AbortSignal): AsyncGenerator<JackEvent> {
  let response: Response
  try {
    response = await fetch(`${JACK_API}/ask`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(asked),
      signal
    })
  } catch (failure) {
    // Unreachable is the common case in development, and saying so beats a spinner that never
    // resolves. `TypeError` is what fetch raises for a connection that never opened.
    if (failure instanceof TypeError) {
      throw new JackUnavailable(
        `Jack is not reachable at ${JACK_API}. Start him with \`uvicorn jack.server:app --port 8100\`.`
      )
    }
    throw failure
  }

  if (!response.ok || !response.body) {
    // The quota answers 429 with a sentence meant for a reader; anything else gets its status.
    const detail = await response.json().catch(() => null)
    throw new JackUnavailable(detail?.detail || `Jack answered ${response.status}.`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE frames are blank-line separated; the tail is whatever has not arrived in full yet.
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.trim()
      if (!line.startsWith('data: ')) continue
      yield JSON.parse(line.slice(6)) as JackEvent
    }
  }
}

/**
 * `off` is the routine state, not a defect: the backend runs on a laptop behind a Cloudflare
 * Tunnel (#219), so the tunnel's 530 and the origin-gone 502 mean "nobody is home". A status
 * from a backend that did answer is `broken` and reads differently.
 */
export type JackHealth = 'checking' | 'ok' | 'off' | 'broken'

/**
 * Probes `/ask`, the one path the tunnel routes, rather than the backend's own `/health`.
 *
 * In production `JACK_API` is the site's own origin and only `/ask*` reaches Jack, so `/health`
 * answers 404 from the static site whether or not he is running: probing it would leave the
 * launcher permanently disabled, reading "answered with an error", against a healthy backend.
 * A 405 here is the proof we want, since `/ask` is POST-only and only the app can refuse the verb.
 */
export async function jackHealth(): Promise<JackHealth> {
  try {
    const response = await fetch(`${JACK_API}/ask`)
    if (response.status === 405 || response.ok) return 'ok'
    // 404 means the request never reached Jack, which reads as away rather than broken.
    const away = [404, 502, 530]
    return away.includes(response.status) ? 'off' : 'broken'
  } catch {
    return 'off'
  }
}
