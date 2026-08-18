# Dependency Standards

**Status:** Binding

Dependencies increase maintenance, security, compatibility, install, and upgrade cost.

## 1. Preference order

Prefer:

1. existing project capability,
2. Python standard library,
3. a small focused dependency,
4. a larger dependency only when it buys meaningful capability.

Do not add infrastructure libraries merely to avoid a small amount of clear code.

## 2. Boundary locality

Provider-specific dependencies should remain localized to the integration that needs them whenever practical.

A provider upgrade forcing unrelated runtime/core edits is a signal that the boundary may be leaking.

## 3. Core dependencies

Core dependencies should remain deliberately minimal.

Adding a dependency to core requires stronger justification than adding one to an adapter.

## 4. Version changes

Dependency upgrades should be reviewable for:

- public API changes,
- runtime behavior changes,
- transitive dependency impact,
- Python compatibility,
- licensing/security implications where relevant.

## 5. Binary assets

Prefer source/vector formats when the consumer supports them.

Track raster assets only when the consumer requires raster output.

Frequently changing generated assets should not create unnecessary repository-history churn.

Do not introduce Git LFS without a measured need and an explicit project decision.
