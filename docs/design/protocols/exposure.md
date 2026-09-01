# Exposure

The composition object that validates bindings, owns their lifecycle, and hosts them.

## API

```python
exposure = deck.expose(Native.http(path="/"), A2A.http(path="/a2a"))

app = exposure.asgi()                       # embed in FastAPI/Starlette
await exposure.serve(host="0.0.0.0", port=8000)   # standalone
```

`expose()` is the only verb on `Deck`; `serve()` lives on the exposure. `deck.serve(...)` sugar is deferred to Phase 6 (`rulings.md` 23).

Every binding on one exposure shares the Deck's agents, Runs, sessions, events and controls: a run started over A2A is visible over native HTTP, namespace permitting. That is a contract test.

## HTTP composition

```text
:8000/
  ├── /api/...    Native
  ├── /a2a/...    A2A
  └── /ui/...     UI
```

Each binding supplies an isolated ASGI app. The host mounts them. No protocol starts Uvicorn.

## Lifecycle

```text
create Deck → create bindings → validate exposure → open Deck → build gateway
→ binding.start() in order → serve → binding.stop() in reverse → close Deck if exposure opened it
```

Every binding's background task runs under the Exposure. If any `start()` fails, the bindings already started stop in reverse, exposure-owned resources close, the Deck closes if the exposure opened it, and the failure is raised.

Ownership: whoever opens something closes it. Mounting onto an open Deck takes no ownership; an `exposure.serve()` that opened the Deck closes it. Same for binding-owned resources.

## Validation before start

Fails at `expose()`, never after a listener is up:

```text
two HTTP bindings claim one path
more than one stdio binding (stdin/stdout is exclusive per process)
a binding whose spi_version is unsupported
a binding whose projection misses a required event category
invalid protocol configuration
```
