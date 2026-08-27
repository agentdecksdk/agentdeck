# Run events stream

This example shows how to observe AgentDeck run lifecycle events as they are
emitted. It starts a `Deck`, streams a run from a simple `Agent`, and prints the
important event kinds a UI or worker could consume.

The example uses `agentdeck.testing.ScriptedModel` so it runs deterministically
without external model credentials.

## Run it

From the repository root:

```bash
uv run python examples/run-events-stream/run.py
```

Expected output includes lifecycle, text, usage, message, and terminal events:

```text
run.started: run_id=... session_id=demo-session
text.delta: 'Hel'
text.delta: 'lo'
usage.reported: total_tokens=...
message.completed: text='Hello'
run.completed: output='Hello'
```

Applications can switch on `event.kind` or inspect the typed `event.payload` to
route events into progress logs, live UI updates, token usage accounting, or
run-completion handlers.
