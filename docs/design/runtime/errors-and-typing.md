# Errors and Typing

Status: Proposed canonical design

This document defines runtime error categories and typed values needed by the Runtime 5.1 surface.

## Error categories

- `RunStateError`: action conflicts with lifecycle state.
- `UnsupportedControlError`: executor/runtime cannot provide the requested capability.
- `NotFoundError`: Run or ask identity does not exist.
- `ValidationError` / `ValueError`: answer or injection cannot be accepted/persisted.
- persistence/store errors.
- projection-invalid/unavailable errors for internal or operator surfaces where relevant.

## No-op vs refusal

No-op means the requested end condition is already true or irrelevant.

Refusal means the caller requested a transition that conflicts with the current state.

Examples:

```text
RUNNING + resume() -> no-op
PAUSED + answer()  -> refusal
```

## Typed runtime values

The public runtime should have explicit types for:

- `RunStatus`
- `Run`
- `Ask`
- `RunTree`
- `RunTreeNode`
- lifecycle/event kinds
- capability snapshot
- injection record if public
- projection metadata if public/internal

## Ask typing

An ask type carries durable routing identity independently from its display payload.

## Terminal typing

Terminal result types must not allow a Run to appear simultaneously completed, failed, and cancelled.
