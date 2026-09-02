# Run events stream

Every AgentDeck execution is a `Run` with an ordered event log. This example streams one and
prints each event as it arrives. **No API key**: the model is scripted in-process, so the run
reaches nothing external and the output below is the same on every machine.

```text
run.py    # deck.stream(...) + print(event.kind, event.payload)
```

No `.agentdeck/` here: the agent is declared inline, because the subject is the stream rather
than project discovery.

```bash
uv venv && source .venv/bin/activate
uv pip install agentdeck-sdk
python run.py
```

```text
run.started: {'invocable': 'Greeter', 'kind_of_invocable': 'agent', 'input': [{'type': 'text', 'text': 'Say hello'}], 'parent_run_id': None}
text.delta: {'message_id': 'msg_scripted_1', 'text': 'Hel'}
text.delta: {'message_id': 'msg_scripted_1', 'text': 'lo'}
usage.reported: {'model': 'fake-scripted', 'usage': {'input_tokens': 3, 'output_tokens': 4, 'usd': None}}
message.completed: {'message_id': 'msg_scripted_1', 'text': 'Hello'}
run.completed: {'output': [{'type': 'text', 'text': 'Hello'}], 'usage': {'input_tokens': 3, 'output_tokens': 4, 'usd': None}}
```

## What to look at

- **`event.kind` is the switch.** A consumer routes on the string: `text.delta` into a live UI,
  `usage.reported` into token accounting, `run.completed` into a completion handler. The payload
  is a typed model per kind, dumped here so one line shows every field it carries.
- **The envelope is the same eight fields for every kind.** `run_id`, `session_id`, `seq` and the
  rest sit on the event, not in the payload, so a store indexes them without parsing.
- **An unfamiliar kind parses, it does not raise.** A newer writer's event lands as an unknown
  payload, which is what lets a released reader survive a schema addition.
- **`agentdeck.testing` is public.** `ScriptedModel` and `patch_model` stub the SDK boundary in
  your own tests the same way they do here; everything above it stays the code under test.

Next: [Runs](https://agentdecksdk.com/runs-and-control/runs) ·
[Events](https://agentdecksdk.com/runs-and-control/events)
