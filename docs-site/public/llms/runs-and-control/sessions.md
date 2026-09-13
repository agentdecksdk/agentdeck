# Sessions

A session is a conversation's identity across multiple runs. Pass the same `session_id` to
`deck.run()` (or `stream`/`runs.start()`) and the agent sees every earlier turn in that
conversation; pass a different one, or none, and the run starts with no memory of any other.

## Continuing a conversation

```python run
import asyncio

from agentdeck import Agent, Deck

assistant = Agent(name="Assistant", instructions="Keep replies to one short sentence.")


async def main() -> None:
    async with Deck(agents=[assistant]) as deck:
        first = await deck.run("Assistant", "My name is Ada.", session_id="conversation-1")
        second = await deck.run("Assistant", "What's my name?", session_id="conversation-1")
        assert first.session_id == second.session_id == "conversation-1"


asyncio.run(main())
```

Both runs share `session_id="conversation-1"`, so the second run's model call carries the first
run's messages along with its own. Every run gets a session either way: leave `session_id` out and
it is scoped to that run's own `run.id`, so nothing carries into the next call.

## What a session carries

A session carries two things, and they are separate: the **event stream** every run on it
appears in, and the **message history** the model is handed. Joining the first is not joining
the second. Only a turn the conversation itself asked an agent for contributes to the message
history: a workflow or tool run passed the same `session_id` is on the stream, and its
input stays out of the transcript, because an orchestration argument is not something a person
said.

That is what makes "enrich, then answer" one conversation:

```python run
import asyncio

from agentdeck import Agent, Deck, WorkflowCtx, workflow

steward = Agent(name="Steward", instructions="Keep replies to one short sentence.")


@workflow
async def discovery(ctx: WorkflowCtx, said: str) -> dict[str, str]:
    """Whatever enrichment a turn needs before the agent answers it."""
    return {"said": said, "length": str(len(said))}


async def main() -> None:
    said = "Hi, I run a studio in Ornit."
    async with Deck(agents=[steward], workflows=[discovery]) as deck:
        enriched = await deck.run("discovery", said, session_id="conversation-3")
        answer = await deck.run("Steward", said, session_id="conversation-3")
        print(enriched["length"], answer.output)


asyncio.run(main())
```

Both runs are on `conversation-3`, so every event either produced carries that `session_id` and
an operator surface keyed by it shows both. The steward's model call still sees `said` exactly
once. Before this, sharing the session was the only way to get the workflow onto the stream, and
it cost the model an extra user message on the first turn.

## One turn per session at a time

A session's history changes while a run is using it, so only one run may hold a session at a time.
Starting a second run against a session that already has one open raises `SessionBusyError`
instead of running against a conversation still being written:

```python run
import asyncio

from agentdeck import Agent, Deck
from agentdeck.errors import SessionBusyError

assistant = Agent(name="Assistant", instructions="Keep replies to one short sentence.")


async def main() -> None:
    async with Deck(agents=[assistant]) as deck:
        first = await deck.runs.start("Assistant", "hello", session_id="conversation-2")
        try:
            await deck.runs.start("Assistant", "hello again", session_id="conversation-2")
        except SessionBusyError as busy:
            print(busy)
        await first


asyncio.run(main())
```

The message names the run holding the session and, when that run is not actually executing, the
call that frees it:

| Holding run's state | Message says | Fix |
|---|---|---|
| Running | session already has a run in flight | wait for it to finish, or give the new turn a different `session_id` |
| Paused | held by run `<id>`, paused | `run.resume()` or `run.cancel()` on that run |
| Waiting for an answer | held by run `<id>`, parked waiting for an answer | `run.answer(...)` or `run.cancel()` on that run |

A run whose process was killed outright never reaches an ending, so without a fix it would hold
its session forever. AgentDeck frees it two ways: a worker sharing `AGENTDECK_CONTROL=sqlite://...`
notices the dead run's lease has lapsed and takes over immediately; failing that, any worker frees
it once the run has gone silent for `AGENTDECK_RUNTIME_STALE_RUN_AFTER_SECONDS` (one hour by
default). A **paused** or **waiting-for-answer** run is never freed this way: it holds its session
until something resumes, answers, or cancels it, however long that takes.

## Where the history lives

| `AGENTDECK_SESSION` | Backend | Notes |
|---|---|---|
| unset (default) | in-process, per session key | lost when the process exits; not shared across workers |
| `redis://...` | Redis | needs `pip install "agentdeck-sdk[redis]"`; shared across processes and workers; survives a restart |

`AGENTDECK_SESSION_REDIS_KEY_PREFIX` and `AGENTDECK_SESSION_REDIS_TTL` tune the Redis backend's key
prefix and per-session expiry; see [Settings](/reference/settings).

## Related

- [Runs](/runs-and-control/runs) - starting a run and rehydrating its handle
- [Lifecycle & Control](/runs-and-control/lifecycle-and-control) - pausing, resuming, and cancelling
- [Deck](/reference/deck) - `session_for()` and injecting a `session_factory`
