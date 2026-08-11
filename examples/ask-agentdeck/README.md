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

## Tests

`tests/test_ask_agentdeck.py`, in the main suite, offline. It checks the composition properties
above and asserts that a representative question surfaces the page that answers it — the part
that rots when a page is renamed, while every import still resolves.
