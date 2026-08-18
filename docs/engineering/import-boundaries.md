# External Import Boundary Registry

**Status:** Current registry

This file records approved exceptions to normal external dependency boundaries.

The architecture rule lives in [`architecture.md`](./architecture.md). This file may change frequently without changing the engineering philosophy.

## Rule

An external SDK import outside its normal integration boundary must be:

- required by a concrete design,
- narrow in scope,
- listed here,
- enforced by tooling where practical.

An existing exception is not precedent for a new one.

## Current exceptions

> Populate this table from the current repository before making this registry binding in CI.

| Path | Allowed external imports | Reason | Enforcement |
|---|---|---|---|
| `agentdeck/testing.py` | narrowly scoped test/provider model types | Drop-in testing model compatibility | ruff/import rules |
| `agentdeck/authoring/` | provider SDK types required for authoring/compilation | Authoring compiles declarations into provider-native forms | import-linter / review |
| `agentdeck/adapters/tools/mcp/` | MCP-related provider types required by the integration | MCP integration boundary | ruff/import rules |

Retired exceptions should be deleted from this table rather than preserved as history.
