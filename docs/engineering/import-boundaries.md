# External Import Boundary Registry

**Status:** Binding, and current as of the tree it was last checked against.

An adapter importing its own external technology is not an exception; that is what the adapter ring is for, and [`.importlinter`](../../.importlinter) enforces which adapter may import what. This file records the places an external SDK is imported **outside** the adapter ring.

The rule lives in [`architecture.md`](./architecture.md) §4. This registry changes whenever the code does.

## Current exceptions

| path | external import | why | enforcement |
|---|---|---|---|
| `agentdeck/authoring/` | `agents` (OpenAI Agents SDK) in `compile.py`, `tools.py`, `hooks.py`, `skills.py`, `web_search.py`, `runners/agent.py` | authoring compiles declarations into provider-native forms, so it holds the provider's types by design | review |
| `agentdeck/testing.py` | `agents`, `openai.types.responses` | `ScriptedModel` substitutes for a real SDK model, so it must satisfy the SDK's own `Model` interface | review |

Anything not in this table must import external SDKs only from within its own adapter.

Retire an entry by deleting its row. Do not keep it as history; git has that.

## Adding a row

A new row means a new exception, and [`architecture.md`](./architecture.md) §4 governs it: narrow, explicit, justified, reviewable. Verify the table against the tree before relying on it:

```bash
grep -rnE "^(from|import) (agents|openai|langfuse|mcp|psycopg|redis)" agentdeck/ --include='*.py' | grep -v "^agentdeck/adapters/"
```

Every line that command prints is either in the table above or a violation.
