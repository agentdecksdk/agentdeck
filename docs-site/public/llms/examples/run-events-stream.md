# Run events stream

Every AgentDeck execution is a `Run` with an ordered event log. This example streams one and prints
each event as it arrives. **No API key**: the model is scripted in-process, so the run reaches
nothing external and the output is the same on every machine.
[Source](https://github.com/agentdecksdk/agentdeck/tree/main/examples/run-events-stream).

No `.agentdeck/` here: the agent is declared inline, because the subject is the stream rather than
project discovery.

```python no-test reason="the example's own run.py, which drives the stream to completion"
import asyncio

from agentdeck import Agent, Deck
from agentdeck.testing import ScriptedModel, patch_model


async def main() -> None:
    deck = Deck(agents=[Agent(name="Greeter", instructions="Answer with a short greeting.")])
    with patch_model(ScriptedModel(deltas=("Hel", "lo"))):
        async with deck:
            async for event in deck.stream("Greeter", "Say hello", session_id="demo-session"):
                print(f"{event.kind}: {event.payload.model_dump(exclude={'kind'})}")


asyncio.run(main())
```

```text
run.started: {'invocable': 'Greeter', 'kind_of_invocable': 'agent', ...}
text.delta: {'message_id': 'msg_scripted_1', 'text': 'Hel'}
text.delta: {'message_id': 'msg_scripted_1', 'text': 'lo'}
usage.reported: {'model': 'fake-scripted', 'usage': {'input_tokens': 3, 'output_tokens': 4, 'usd': None}}
message.completed: {'message_id': 'msg_scripted_1', 'text': 'Hello'}
run.completed: {'output': [{'type': 'text', 'text': 'Hello'}], 'usage': {...}}
```

## What to look at

- **`event.kind` is the switch.** A consumer routes on the string: `text.delta` into a live UI,
  `usage.reported` into token accounting, `run.completed` into a completion handler. The payload is
  a typed model per kind.
- **The envelope is the same for every kind.** `run_id`, `session_id`, `seq` and the rest sit on the
  event rather than in the payload, so a store indexes them without parsing.
- **An unfamiliar kind parses, it does not raise.** A newer writer's event lands as an unknown
  payload, which is what lets a released reader survive a schema addition.
- **`agentdeck.testing` is public.** `ScriptedModel` and `patch_model` stub the SDK boundary in your
  own tests the same way they do here.

Next: [Runs](/runs-and-control/runs) · [Events](/reference/events)
