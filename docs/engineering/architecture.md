# Architecture Standards

**Status:** Binding for dependency direction and ownership.

This document describes current architectural law. Historical migrations and retired structures belong in Git history or design docs.

## 1. Direction

AgentDeck should preserve a clear inward dependency direction:

```text
Authoring / Composition / Surfaces
                ↓
             Runtime
                ↓
          Core contracts
                ↑
             Ports
                ↑
            Adapters
```

The exact package layout may evolve. The ownership rules should remain stable.

## 2. Core

Core defines AgentDeck's stable language:

- events,
- statuses,
- context/value objects,
- control primitives,
- contracts/ports,
- shared errors where appropriate.

Core should know as little as possible about the outside world.

It must not depend on concrete executors, storage products, telemetry SDKs, HTTP frameworks, or CLI frameworks.

Keep the core dependency set deliberately small.

## 3. Runtime

Runtime owns execution policy.

Examples:

- lifecycle,
- routing,
- sequencing of operations,
- control handling,
- persistence coordination,
- ownership and cleanup.

Runtime depends on contracts, not integration implementations.

If runtime needs to know which concrete provider is installed, the boundary is probably wrong.

## 4. Adapters

Adapters translate between AgentDeck contracts and external systems.

They own:

- external SDK types,
- provider-specific lifecycle,
- provider-specific exceptions,
- translation into/from AgentDeck contracts.

External assumptions should stop at the adapter boundary whenever practical.

Adapters should not depend on another adapter's implementation.

Useful test:

> Removing one integration should not damage unrelated AgentDeck functionality.

## 5. Authoring and composition

Authoring constructs user-facing definitions.

Composition assembles concrete runtime dependencies.

Compilation or provider-specific authoring logic may legitimately depend on provider SDKs when that is the cleanest model, but such dependencies must be intentional and registered rather than spreading accidentally.

Do not create ports solely to achieve theoretical purity when no meaningful substitution boundary exists.

## 6. Surfaces

CLI, HTTP, ASGI, protocol, and UI-facing surfaces expose AgentDeck.

They may translate transport concerns, but must not invent alternate run semantics.

A surface should delegate lifecycle and runtime behavior to the same underlying model used by Python callers.

## 7. `__init__` and imports

- Prefer implementation in named modules.
- Package `__init__` files primarily define public re-exports.
- Avoid wildcard imports.
- Avoid import-time I/O or client construction.
- Prefer imports from the defining module inside the implementation unless a package-level import is intentionally the stable contract.

## 8. Architecture exceptions

Exceptions are acceptable when they make the design materially cleaner.

An exception must be:

- narrow,
- explicit,
- justified,
- reviewable,
- represented in [`import-boundaries.md`](./import-boundaries.md) when it concerns external dependency boundaries.

Never treat an old exception as precedent automatically.
