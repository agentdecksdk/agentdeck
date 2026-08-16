# AgentDeck Engineering Principles

**Status:** Binding philosophy

## 1. We do the hard work; the user gets the short path

AgentDeck should absorb runtime and integration complexity when it can do so reliably.

The public API should express user intent, not the machinery required to execute it.

> **Simple outside. Elegant inside.**

## 2. Simplicity is earned

> “Simple can be harder than complex: you have to work hard to get your thinking clean to make it simple.”
> — Steve Jobs, *BusinessWeek*, 1998

Do not stop at the first working abstraction.

Understand the problem, identify the invariants, and look for the smallest coherent model that explains the behavior correctly.

The goal is not minimum code. The goal is minimum necessary complexity.

## 3. Start from the developer experience

> “You've got to start with the customer experience and work backwards to the technology.”
> — Steve Jobs, WWDC 1997

For AgentDeck, the customer is the developer.

Design the experience we want the developer to have first. Then make runtime services, ports, stores, adapters, engines, and infrastructure serve it.

An internal primitive does not automatically deserve to become a public concept.

A port signature does not automatically deserve to become a public API.

An implementation requirement does not automatically become user configuration.

## 4. Complexity must earn its place

Before introducing a new abstraction, state, port, service, registry, configuration option, or public concept, ask:

- What concrete problem does this solve?
- Can an existing primitive solve it?
- Does this remove complexity or merely move it?
- What failure modes does it introduce?
- Does the user gain meaningful capability?
- Can the same behavior be expressed with a smaller model?

## 5. Strong primitives over many mechanisms

Prefer a small number of concepts that compose well.

A good primitive makes several behaviors feel natural without requiring separate APIs for each one.

Public concepts should age slowly even when internals evolve quickly.

## 6. Abstractions must delete complexity

An abstraction is useful when the caller can know less after it exists.

If it only renames or relocates the same cognitive burden, it has not earned its place.

## 7. One obvious path first

Common operations should have one clear recommended path.

Advanced knobs are escape hatches, not prerequisites.

Good defaults are product design.

## 8. Interoperate; do not trap

AgentDeck should wrap and compose existing systems where practical instead of forcing users to rebuild them.

Use the AgentDeck abstraction for the common path while preserving access to native capabilities when needed.

## 9. Correctness before convenience; convenience after correctness

A simple API that behaves unpredictably is not simple.

Establish clear invariants, ownership, lifecycle rules, concurrency semantics, and failure behavior first. Then hide the machinery behind a convenient surface.

## 10. Prefer elegant internal models

The ideal implementation is not a tiny public API backed by an internal nightmare.

Prefer a small number of strong internal rules over layers of cleverness.

Readable, testable, boring code is often better than impressive code.

## 11. Delete aggressively

Removing unnecessary concepts, configuration, layers, special cases, and duplicated policy is a feature.

The project should become conceptually cleaner as it becomes more capable.

## 12. Engineering north star

When uncertain:

```text
Correct
↓
Simple
↓
Clear
↓
Maintainable
↓
Extensible when justified
```

Not:

```text
Abstract
↓
Generic
↓
Configurable
↓
Complex
↓
Eventually understandable
```

> **Do the hard thinking. Find the underlying model. Build the smallest coherent solution. Give the user the short path.**
