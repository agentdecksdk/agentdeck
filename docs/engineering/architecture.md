# Architecture Standards

**Status:** Binding.

Dependency *direction* is enforced by [`.importlinter`](../../.importlinter): 10 contracts, run by `make check` and by CI. That file is the authority and it fails the build, so this one does not restate it. What follows is *ownership*, which no tool can check.

## 1. Rings

```text
Authoring / Composition / Surfaces
                ↓
             Runtime
                ↓
          Core contracts (agentdeck/core/ports/)
                ↑
            Adapters
```

`Deck` is the single composition root. The package layout may evolve; the ownership below may not.

## 2. Ownership

| ring | owns | never owns |
|---|---|---|
| `core/` | events, statuses, value objects, control primitives, ports, the error taxonomy | any concrete executor, store, SDK or transport |
| `runtime/` | lifecycle, routing, sequencing, control handling, persistence coordination, cleanup | knowledge of which concrete provider is installed |
| `adapters/` | one external technology each: its SDK types, its lifecycle, its exceptions, and translation to and from core contracts | another adapter's implementation |
| `authoring/` | user-facing declarations compiled to specs | run semantics |
| `surfaces/` | transport translation for CLI, HTTP, ASGI and protocol ingress | alternate run semantics. A surface delegates lifecycle to the same model a Python caller uses |

Two tests that settle most boundary arguments:

- Removing one integration must not damage unrelated functionality.
- If `runtime/` needs to know which provider is installed, the boundary is in the wrong place.

Do not add a port where no substitution boundary exists. A port with one implementation is a port that has not earned itself.

## 3. `__init__` and imports

- Implementation lives in named modules. `__init__` files define public re-exports and little else.
- No wildcard imports.
- No import-time I/O and no client construction at import time.
- Import from the defining module unless the package-level import is deliberately the stable contract.

### The public import surface

| path | holds |
|---|---|
| `agentdeck` | the everyday vocabulary: `Agent`, `Deck`, `Run`, `tool`, `workflow`, the contexts, the content blocks, `Observer`, `views` |
| `agentdeck.<feature>` | one cohesive feature's API: `errors`, `observers`, `skills`, `mcp`, `bindings`, `testing` |
| `agentdeck.core.*`, `agentdeck.runtime.*`, `agentdeck.adapters.*`, `agentdeck.authoring.*` | internal |

One canonical path per public concept, and no internal path in user-facing docs or examples.
A name lives at the root or in one feature namespace, never both: an alias in a second namespace
is one more path to keep true. `agentdeck.errors` owns the whole taxonomy, so the root carries
only `AgentdeckError`. `tests/test_public_surface.py` pins the root's `__all__`, so widening it
is a deliberate diff.

## 4. Exceptions

An exception is acceptable when it makes the design materially cleaner. It must be narrow, explicit, justified, reviewable, and recorded in [`import-boundaries.md`](./import-boundaries.md) when it crosses an external dependency boundary.

An old exception is never precedent for a new one.
