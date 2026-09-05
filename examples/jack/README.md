# Jack

The assistant that answers questions about AgentDeck, built on AgentDeck. It is the reference
application for v3 (#219): a real small program against the public surface, kept small enough to
read in one sitting.

```bash
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
python run.py "how do I create an agent?"                    # one question, headless
uvicorn jack.server:app --port 8100                          # the route the docs panel calls
```

## Tracing and the event log

Jack keeps no record of himself unless he is configured to. Both switches are environment
variables, and both are off by default so a clone runs without a backend.

| Variable | Off (default) | On |
|---|---|---|
| `AGENTDECK_EVENTS` | `memory://`, so the log dies with the process | `sqlite:///path/events.sqlite3` writes every run to disk |
| `AGENTDECK_LANGFUSE_PUBLIC_KEY` | no observer is attached | each run becomes a Langfuse trace, built from the canonical event log |

With Langfuse configured, also set `AGENTDECK_LANGFUSE_SECRET_KEY`, `AGENTDECK_LANGFUSE_BASE_URL`
and `AGENTDECK_LANGFUSE_ENVIRONMENT`. `observers()` in `server.py` reads the public key and
decides; nothing else in the application changes.

Both switches store what visitors typed. This endpoint is unauthenticated, so treat the store and
the tracing project as holding third-party text: keep the credentials out of the repository, keep
the database unreadable to other users, and decide a retention period before turning either on.

## What it is

One agent, three tools, one context.

| File | What is in it |
|---|---|
| `jack/corpus.py` | `DocsCorpus`  -  every `.mdx` page under `docs-site/content/`, read once and keyed by the slug the site serves it under |
| `jack/agent.py` | the three tools, the instructions, and the `Agent(...)` |
| `jack/server.py` | one `POST /ask` route over `deck.stream()`, streaming canonical events |
| `run.py` | composes the `Deck` and asks one question |

```bash
curl -N -X POST localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"explain what this page is for","page":"concepts/skills"}'
```

## The three things worth copying

**Search is a dict and a scan.** The corpus is 22 pages and about 120 KB, so there is no vector
store, no embedding model, no index to rebuild when a page changes  -  TF-IDF over
`Path.read_text()`, in about thirty lines. At this size it is faster than an embedding round-trip
and it cannot return a stale chunk. `DocsCorpus.search` is where that decision lives, and its
signature is what stays fixed if the corpus ever outgrows it.

**The tools take a context, so they are `@tool`.**

```python
@tool
def read_doc(slug: str, docs: ToolCtx[DocsCorpus]) -> str:
    """Read one AgentDeck documentation page in full, by its slug."""
    return docs.data.pages.get(slug, ...)
```

Not `@function_tool`: that decorator would put `docs` into the schema the model sees, and the
model has no `DocsCorpus` to pass. `@tool` compiles it instead, and the model is offered only
`slug`  -  and is required here precisely because `docs` is a `ToolCtx`: a plain function carrying
one is refused at `build()`, naming `@tool` as the fix.

**The context type is declared, so the deck checks it.**

```python
async with Deck(agents=[ask], context=DocsCorpus) as deck:
    result = await deck.run("Jack", question, context=corpus)
```

`context=DocsCorpus` is the *type*; the instance goes in per run. Declaring it makes `build()`
check every `ToolCtx[...]` in the catalog  -  both tools and the instructions callable  -  before a
question is ever asked. Declaring the wrong type raises `ContextTypeError` naming both, which
`tests/test_jack.py` pins.

## Why it serves itself instead of using `Deck.asgi()`

agentdeck packages an HTTP surface as bindings. This application cannot use it, for two
independent reasons  -  and finding that out is a large part of what this example exists for.

- **A run through a binding carries `context=None`.** There is no wire form for a live Python
  object, so the packaged surface cannot deliver one. Both tools here need the `DocsCorpus`.
- **`Native.http()` speaks a different wire.** It's AgentDeck's generic protocol (`POST /runs`,
  `GET /runs/{id}/events`, and the rest), not a single `POST /ask` with a chat body. Adopting it
  still means translating between this application's request and the protocol's, which is the
  layer this route exists to avoid.

So `server.py` is forty lines of FastAPI over `deck.stream()`. Each SSE frame is one canonical
`Event`, dumped as written  -  no translation layer, so a browser switching on `event.kind` reads
exactly what a later process reading the run back would read.

The page context travels as **text**, not as a `ToolCtx` and not as a `DataBlock`:

```
<context>
The reader is on the documentation page: concepts/skills
</context>

explain what this page is for
```

A `ToolCtx[T]` cannot cross HTTP  -  that is the documented boundary, and the right one, because a
page slug is data a browser sent rather than a live object this server owns. `DataBlock` is the
typed way to put JSON in an input and the engine refuses it: *"cannot send a 'data' block to the
model; it accepts text, image, and audio."* Both are recorded as findings in
`docs/delivery/plan-219-delivery.md`.

## Why `Deck(agents=[...])` and not `Deck.from_project()`

Both are front doors onto the same catalog, and the other examples here use `from_project`. This
one cannot, and the reason is worth knowing before you hit it:

**the agent's tools and the deck's context type are the same `DocsCorpus` class**, and a
`.agentdeck/` bundle has no clean way to share a type with the program that composes it.

- A bundle importing a module that sits *next to* `.agentdeck/` works only when the process was
  started from that directory  -  `Deck.from_project()` from anywhere else raises
  `ConfigError: agents/x/agent.py failed to import: No module named 'shared'`. A server is
  started from wherever its supervisor happens to be.
- Putting the shared module *inside* `.agentdeck/` does work: the project directory is mounted
  as a package, so `from agentdeck_project.shared import DocsCorpus` resolves from the bundle and
  from the host. But `agentdeck_project` is an internal alias, not public API, and it only exists
  *after* `from_project()` has run  -  so the host program cannot import from it at module level,
  which is where imports go.

Explicit composition has neither problem: `jack` is an ordinary Python package, imported
the ordinary way, from any working directory. This is what a real embedded application looks
like, and it is recorded as a finding rather than smoothed over  -  see
`docs/delivery/plan-219-delivery.md`.

## Serving it publicly

The docs site is a static bundle on GitHub Pages; this backend runs on a machine you own, reached
through a Cloudflare Tunnel. Availability therefore depends on that machine being up, which is
the deliberate trade for not hosting a model-calling service.

```bash
uvicorn jack.server:app --port 8100                     # binds 127.0.0.1
cloudflared tunnel run --token <TOKEN>                    # agentdecksdk.com/ask -> :8100
```

There are two ways to run the tunnel and they differ in **where the routing rules live**, which
matters here because this README makes claims about what is exposed.

- **Dashboard-managed** (what AgentDeck's own instance uses). The tunnel is created in Zero Trust
  → Networks → Tunnels, runs from a token, and its public hostname and service are configured in
  Cloudflare. Convenient, and the rules are not in this repo  -  so nobody reviewing a change here
  can see them. If you run it this way, the dashboard must say exactly one public hostname,
  `agentdecksdk.com` with a `/ask` path route → `http://localhost:8100`, and nothing else.
- **Locally-managed** (`cloudflared.yml`, committed here). The tunnel is created with
  `cloudflared tunnel create`, and the ingress list  -  one hostname, one port, `http_status:404`
  catch-all  -  is a reviewable file. This is the reproducible form: a reader copying this example
  cannot copy someone else's dashboard.

Both are fine. What is not fine is the two disagreeing, because everything below describes the
rules in `cloudflared.yml`.

Set `JACK_ORIGINS` to the site's origin, and the `JACK_API_URL` repository
variable to `https://agentdecksdk.com`  -  the panel appends `/ask` itself, and the Pages
build bakes the origin in as
`NEXT_PUBLIC_AGENTDECK_API_URL`. **It must be `https`**: Pages is served over TLS and a browser
hard-blocks an HTTPS page calling `http://`, which is the real reason a tunnel is required rather
than a port forward.

### What is exposed, and what is not

Worth being precise about, because this is an unauthenticated endpoint that spends money.

| | |
|---|---|
| **Reachable through the tunnel** | `localhost:8100`, and nothing else  -  *provided the ingress in force says so*, which is `cloudflared.yml` locally or the public-hostname list in the dashboard. One hostname, ending in `http_status:404`, so an unlisted hostname is refused rather than proxied. The tunnel itself is an outbound connection: no inbound port, no firewall rule, nothing else you run locally becomes reachable. |
| **Bind address** | `127.0.0.1`, uvicorn's default. Do not pass `--host 0.0.0.0`  -  that publishes the assistant to your whole network *in addition* to the tunnel, and is the one way this setup leaks past what is written here. |
| **On the wire** | An allowlist of five event kinds. `tool.call.completed` is deliberately not among them: its `result_preview` is the tool's output verbatim, so a tool that raised would put its exception text on a public wire. `usage.reported` is dropped too  -  the model name and per-turn token counts are nobody else's business. |
| **Failure messages** | `run.failed` carries the exception's *type name* and the engine's, never its text. That is agentdeck's own design, not something this application adds. |
| **The API key** | Only in the backend process. The static bundle is public by construction; `NEXT_PUBLIC_*` is readable in the shipped JavaScript, and nothing but the API URL goes there. |
| **The tools** | `read_doc` is a dict lookup on a slug, not a file read, so no slug reaches the filesystem and there is no path to traverse. The corpus is the published documentation, which is public anyway. |
| **Cost** | **Three conversations per client per rolling day, twenty turns each**  -  sixty turns is the hard ceiling on what this can be made to spend. Plus 2000-character caps on `question` and `selection`, and a 900-token cap on the answer. CORS is *not* a control here: it constrains browsers and a `curl` ignores it. |

The quota is deliberately shaped as *conversations* rather than a flat request count, because
the two halves stop different things. **Turns per session** bound the conversation: a session
re-sends its whole history to the model every turn, so an unbounded one costs quadratically
while its context window fills with the caller's own text  -  that is how you overload this
without ever sending a long message. **Sessions per day** stop the obvious way around that,
which is to finish twenty turns and start again. A caller who omits `session_id` gets one bucket
rather than a free pass, or the whole quota would be opt-in.

`JACK_SESSIONS_PER_DAY` and `JACK_TURNS_PER_SESSION` change the numbers. The
counting is in-process and per-IP, which is right for one backend behind one tunnel and wrong
the moment there are two replicas or a caller with addresses to spare  -  the upgrade for that is
a Cloudflare rate-limiting rule at the edge, where the traffic never reaches the machine.

### "Only from the docs site"  -  what that can and cannot mean

The honest ceiling first: **this cannot be fully enforced.** The caller is a reader's browser,
not GitHub's servers, so requests arrive from arbitrary addresses; and `Origin` is a header a
browser sets truthfully and anything else forges in one flag. Nothing short of authentication
distinguishes "a browser on the docs site" from "a script claiming to be one", and a credential
shipped in a public bundle is not a credential.

What is in place, and what each layer actually buys:

| Layer | Stops | Does not stop |
|---|---|---|
| **`Origin` checked in the route**  -  `403` before the model is called | Another website embedding this endpoint; casual reuse; every cross-origin browser request | A `curl` that sets the header |
| **CORS** | A browser handing the response back to another site's JavaScript | Anything, from the cost side  -  the run has already happened by the time CORS applies. This is why the check above is in the route and not left to middleware |
| **The quota**  -  3 conversations × 20 turns per client per day | The forged-header case from being *worth* it | The first sixty turns |

Together these mean the realistic attack is "someone forges a header and gets sixty turns a
day", which is a bounded annoyance rather than an open relay.

**If that is not good enough, the answer is [Cloudflare Turnstile](https://developers.cloudflare.com/turnstile/),**
not more headers. The docs page renders a widget, the browser solves it invisibly, and the
backend verifies the resulting token against Cloudflare with a secret that never leaves the
server. That genuinely separates a real browser on your page from a script, which is the thing
`Origin` only gestures at. It is deliberately *not* implemented here: it adds a client-side
dependency and a per-request verification call to a reference application whose job is to
demonstrate AgentDeck, and the quota already bounds the damage. Add it when the endpoint costs
more than it is worth.

### Prompt injection: what is guarded, and what is not

The browser is attacker-controlled, so `page` and `selection` are attacker-controlled. Being
straight about which half of this is actually solved:

**Guarded  -  the structure of the prompt cannot be forged.**

- `page` is checked against the corpus and dropped unless it names a real page. The only values
  that survive are 22 known strings, which removes the field as a vector rather than sanitising
  it.
- `selection` is arbitrary text and cannot be allowlisted, so the `<context>` delimiter is
  stripped from it. Without that, a planted `</context>` closes the block early and everything
  after it reads to the model as instruction rather than as quoted material.
- Both are pinned by tests, because this is the class of thing that regresses silently.

**Not guarded  -  what the model chooses to say.**

The topic rule ("answer questions about AgentDeck, from the documentation") is an *instruction*,
and instructions are persuadable. Someone determined can get this endpoint to write them a poem.
There is no topic classifier, no output filter, and no jailbreak detection, and adding a
model-graded guard would double the cost and latency of every question to prevent something whose
worst outcome is an off-topic paragraph.

That is a deliberate trade, and it rests on the blast radius being small by construction:

- **The agent has three tools and all are read-only over public documentation.** There is no
  write, no shell, no network, no database. Nothing a jailbreak reaches is anything a reader
  could not already get by browsing the site.
- **`read_doc` is a dict lookup on a slug**, not a file read  -  so no input reaches the
  filesystem and there is no path to traverse.
- **The cost is capped** by the rate limit and the length caps, which is the abuse that actually
  matters here.

So the realistic worst case is a rate-limited stranger making the assistant say something silly
under your domain name. If that becomes unacceptable, the fix is an edge rule or a cheap
classifier ahead of the run  -  not more prompt text, which is what the model is already ignoring
in that scenario.

## Tests

`tests/test_jack.py`, in the main suite, offline. It checks the composition properties
above and asserts that a representative question surfaces the page that answers it  -  the part
that rots when a page is renamed, while every import still resolves.
