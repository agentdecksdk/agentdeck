# Errors

Every error states what happened, why, and the exact action that resolves it. The reader is a user at 2am, not the author.

Good (real, `authoring/native.py`):

```text
{name} is declared @workflow but is not async. A native workflow is awaited by
the runtime, and a blocking body would stall every run sharing its event loop:
make it `async def`, and use asyncio.to_thread for work that genuinely blocks.
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
