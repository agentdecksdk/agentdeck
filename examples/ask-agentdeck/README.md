# Ask AgentDeck

The assistant that answers questions about AgentDeck, built on AgentDeck. It is the reference
application for v3 (#219): a real small program against the public surface, kept small enough to
read in one sitting.

```bash
export OPENAI_MODEL=gpt-4.1-mini OPENAI_API_KEY=sk-...
python run.py "how do I create an agent?"                    # one question, headless
uvicorn ask_agentdeck.server:app --port 8100                 # the route the docs panel calls
```

## What it is

One agent, two tools, one context.

| File | What is in it |
|---|---|
| `ask_agentdeck/corpus.py` | `DocsCorpus` — every `.mdx` page under `docs-site/content/`, read once and keyed by the slug the site serves it under |
| `ask_agentdeck/agent.py` | the two tools, the instructions, and the `Agent(...)` |
| `ask_agentdeck/server.py` | one `POST /ask` route over `deck.stream()`, streaming canonical events |
| `run.py` | composes the `Deck` and asks one question |

```bash
curl -N -X POST localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"explain what this page is for","page":"concepts/skills"}'
```

## The three things worth copying

**Search is a dict and a scan.** The corpus is 22 pages and about 120 KB, so there is no vector
store, no embedding model, no index to rebuild when a page changes — TF-IDF over
`Path.read_text()`, in about thirty lines. At this size it is faster than an embedding round-trip
and it cannot return a stale chunk. `DocsCorpus.search` is where that decision lives, and its
signature is what stays fixed if the corpus ever outgrows it.

**The tools take a context, so they are plain functions.**

```python
def read_doc(slug: str, docs: Context[DocsCorpus]) -> str:
    """Read one AgentDeck documentation page in full, by its slug."""
    return docs.data.pages.get(slug, ...)
```

No `@function_tool`. A tool that declares a `Context` parameter must stay undecorated, because
the decorator would put that parameter into the schema the model sees — and the model has no
`DocsCorpus` to pass. `build()` compiles it instead, and the model is offered only `slug`.

**The context type is declared, so the deck checks it.**

```python
async with Deck(agents=[ask], context=DocsCorpus) as deck:
    result = await deck.run("AskAgentDeck", question, context=corpus)
```

`context=DocsCorpus` is the *type*; the instance goes in per run. Declaring it makes `build()`
check every `Context[...]` in the catalog — both tools and the instructions callable — before a
question is ever asked. Declaring the wrong type raises `ContextTypeError` naming both, which
`tests/test_ask_agentdeck.py` pins.

## Why it serves itself instead of using `Deck.asgi()`

agentdeck packages an HTTP surface. This application cannot use it, for two independent reasons —
and finding that out is a large part of what this example exists for.

- **A run through `asgi()` carries `context=None`.** There is no wire form for a live Python
  object, so the packaged surface cannot deliver one. Both tools here need the `DocsCorpus`.
- **Its chat body is exactly `{"session_id", "message"}`**, frozen byte-for-byte by
  `tests/golden/`. The page the reader is on has nowhere to go in it.

So `server.py` is forty lines of FastAPI over `deck.stream()`. Each SSE frame is one canonical
`Event`, dumped as written — no translation layer, so a browser switching on `event.kind` reads
exactly what a later process reading the run back would read.

The page context travels as **text**, not as a `Context` and not as a `DataBlock`:

```
<context>
The reader is on the documentation page: concepts/skills
</context>

explain what this page is for
```

A `Context[T]` cannot cross HTTP — that is the documented boundary, and the right one, because a
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
  started from that directory — `Deck.from_project()` from anywhere else raises
  `ConfigError: agents/x/agent.py failed to import: No module named 'shared'`. A server is
  started from wherever its supervisor happens to be.
- Putting the shared module *inside* `.agentdeck/` does work: the project directory is mounted
  as a package, so `from agentdeck_project.shared import DocsCorpus` resolves from the bundle and
  from the host. But `agentdeck_project` is an internal alias, not public API, and it only exists
  *after* `from_project()` has run — so the host program cannot import from it at module level,
  which is where imports go.

Explicit composition has neither problem: `ask_agentdeck` is an ordinary Python package, imported
the ordinary way, from any working directory. This is what a real embedded application looks
like, and it is recorded as a finding rather than smoothed over — see
`docs/delivery/plan-219-delivery.md`.

## Serving it publicly

The docs site is a static bundle on GitHub Pages; this backend runs on a machine you own, reached
through a Cloudflare Tunnel. Availability therefore depends on that machine being up, which is
the deliberate trade for not hosting a model-calling service.

```bash
uvicorn ask_agentdeck.server:app --port 8100                     # binds 127.0.0.1
cloudflared tunnel --config cloudflared.yml run agentdeck-ask    # ask.agentdecksdk.com -> :8100
```

Set `ASK_AGENTDECK_ORIGINS` to the site's origin, and the `ASK_AGENTDECK_API_URL` repository
variable to `https://ask.agentdecksdk.com` — the Pages build bakes it in as
`NEXT_PUBLIC_AGENTDECK_API_URL`. **It must be `https`**: Pages is served over TLS and a browser
hard-blocks an HTTPS page calling `http://`, which is the real reason a tunnel is required rather
than a port forward.

### What is exposed, and what is not

Worth being precise about, because this is an unauthenticated endpoint that spends money.

| | |
|---|---|
| **Reachable through the tunnel** | `localhost:8100`, and nothing else. `cloudflared.yml` lists one hostname and ends in `http_status:404`, so an unlisted hostname is refused rather than proxied. The tunnel is an outbound connection: no inbound port, no firewall rule, nothing else you run locally becomes reachable. |
| **Bind address** | `127.0.0.1`, uvicorn's default. Do not pass `--host 0.0.0.0` — that publishes the assistant to your whole network *in addition* to the tunnel, and is the one way this setup leaks past what is written here. |
| **On the wire** | An allowlist of five event kinds. `tool.call.completed` is deliberately not among them: its `result_preview` is the tool's output verbatim, so a tool that raised would put its exception text on a public wire. `usage.reported` is dropped too — the model name and per-turn token counts are nobody else's business. |
| **Failure messages** | `run.failed` carries the exception's *type name* and the engine's, never its text. That is agentdeck's own design, not something this application adds. |
| **The API key** | Only in the backend process. The static bundle is public by construction; `NEXT_PUBLIC_*` is readable in the shipped JavaScript, and nothing but the API URL goes there. |
| **The tools** | `read_doc` is a dict lookup on a slug, not a file read, so no slug reaches the filesystem and there is no path to traverse. The corpus is the published documentation, which is public anyway. |
| **Cost** | Rate-limited per client, `ASK_AGENTDECK_RATE_LIMIT` questions per five minutes, plus length caps on every field. CORS is *not* a control here — it constrains browsers and a `curl` ignores it. For anything beyond one process behind one tunnel, add a Cloudflare rate-limiting rule at the edge so the traffic never reaches the machine. |

## Tests

`tests/test_ask_agentdeck.py`, in the main suite, offline. It checks the composition properties
above and asserts that a representative question surfaces the page that answers it — the part
that rots when a page is renamed, while every import still resolves.
