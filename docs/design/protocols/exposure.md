# Exposure

The composition object that validates bindings, owns their lifecycle, and hosts them.

Status: proposed, 2026-08-29.

## API

```python
exposure = deck.expose(Native.http(path="/"), A2A.http(path="/a2a"))

app = exposure.asgi()                       # embed in FastAPI/Starlette
await exposure.serve(host="0.0.0.0", port=8000)   # standalone
```

`expose()` is the only verb on `Deck`; `serve()` lives on the exposure. `deck.serve(...)` sugar is deferred to Phase 6 (`rulings.md` 23).

## Many protocols, one Deck

```python
await deck.expose(Native.http(path="/"), A2A.http(path="/a2a"), AGUI.http(path="/ui")).serve()
```

Same agents, Runs, sessions, events and controls. A run started over A2A is visible over native HTTP, namespace and authorization permitting. That is a contract test.

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
→ start bindings → serve → stop bindings → close Deck if exposure opened it
```

Ownership: whoever opens something closes it. Mounting onto an already-open Deck takes no ownership of it; `exposure.serve()` that opened the Deck closes it. Same rule for binding-owned resources.

## Validation before start

Fails at `expose()`, never after a listener is up:

```text
two HTTP bindings claim one path
more than one stdio binding (stdin/stdout is exclusive per process)
a binding whose spi_version is unsupported
a binding whose projection misses a required event category
invalid protocol configuration
```

stdio and HTTP bindings may share one exposure: one stdio binding at most, HTTP bindings on one listener.

## Atomic startup

If any binding fails to start, every started binding stops, exposure-owned resources close, the Deck closes if exposure opened it, and the failure is raised. Never half a protocol set running.
