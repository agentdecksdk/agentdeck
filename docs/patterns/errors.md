# Errors

Every error states what happened, why, and the exact action that resolves it. The reader is a user at 2am, not the author.

Good (real, `adapters/engines/langgraph/engine.py`):

```text
{name} paused at a node boundary but is durable=False: with no checkpointer
the paused run cannot be resumed. Set `durable = True` on the workflow.
```

Good (real, `runtime/registry.py`): a lookup failure names the alternatives:

```python
raise NotFoundError(f"No {self.label} named {name!r}. Available: {sorted(plugins)}.")
```

Bad: `raise ValueError("invalid workflow state")`, states none of the three.

Rules:
- Raise from the core taxonomy (`core` errors, `AgentdeckError` subclasses), never bare `Exception`.
- The fix goes in the message, as code the user can type.
- A signal is not an error: `ControlSignalled` exists because cancel/pause honored-as-asked must not read as failure.
