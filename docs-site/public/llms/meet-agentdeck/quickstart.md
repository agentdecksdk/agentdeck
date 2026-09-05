# Quickstart

Build a Deck, start a Run, and watch its events.

<Steps>

## Install

```bash
pip install agentdeck-sdk
```

## Build your deck

Compose an agent into a Deck:

```python
from agentdeck import Agent, Deck

agent = Agent(
    name="assistant",
    model="gpt-4o-mini",
    instructions="You are a concise assistant.",
)

deck = Deck(agents=[agent])
```

## Start a run

Execute the agent within the Deck's runtime context:

```python
async def main():
    async with deck:
        run = await deck.runs.start("assistant", input="Hello!")
        async for event in run.events(follow=True):
            print(event.kind)
        result = await run
        print("Status:", await run.status())
        print("Result:", result.output)
```

`follow=True` streams until the run reaches a terminal event. Without it you get only what the
log already holds, which for a run this young is one event. `run.status()` is a coroutine, not a
property.

## Watch what happened

Running the script emits an ordered sequence of lifecycle and content events:

```text
run.started
text.delta
usage.reported
message.completed
run.completed

Status: completed
Result: Hello!
```

`text.delta` is one streamed fragment and there is usually more than one; `message.completed`
carries the finished text. Every kind a run can emit is listed in the
[events reference](/reference/events).

</Steps>

## What you just used

- **Agent**: Your executable component.
- **Deck**: The composition root for your agents, workflows, tools, and skills.
- **Run**: A first-class execution you can observe and control.
- **Events**: The ordered record of what happened during that Run.

<BrandCallout type="runtime" title="A RUN IS CONTROLLABLE">
A Run is not just a return value. It is a living, controllable execution with safe-point pause, resume, and cancellation:

```python
await run.pause()
await run.resume()
await run.cancel()
```
</BrandCallout>

## If something went wrong

The first-run failures, by what your terminal says. Open the one that matches.

<details>
<summary>OPENAI_API_KEY is not set</summary>

```text
openai.OpenAIError: The api_key client option must be set either by passing api_key to the
client or by setting the OPENAI_API_KEY environment variable
```

The run then surfaces as `RuntimeError: run '<run-id>' failed: OpenAIError in engine 'openai-agents'`.
The key is missing from the Python process running the Deck, so export it before running the
script, and set the same variable in your IDE's run configuration if you start it from there:

```bash
export OPENAI_API_KEY=sk-...
```

</details>

<details>
<summary>The model does not exist, or your key cannot reach it</summary>

```text
openai.NotFoundError: Error code: 404 - {'error': {'message': 'The model
`not-a-real-agentdeck-model` does not exist or you do not have access to it.', 'type':
'invalid_request_error', 'param': 'model', 'code': 'model_not_found'}}
```

The run then surfaces as `RuntimeError: run '<run-id>' failed: NotFoundError in engine 'openai-agents'`.
The `model` on the `Agent`, or `OPENAI_MODEL`, names something your key or configured
OpenAI-compatible endpoint cannot reach. Use a model your account has, such as this page's
`gpt-4o-mini`, or update `OPENAI_BASE_URL` and `OPENAI_MODEL` together when you point at a gateway.

</details>

<details>
<summary>A coroutine was never awaited</summary>

```text
RuntimeWarning: coroutine 'Runs.start' was never awaited
```

`deck.runs.start(...)` is async: calling it without `await` builds a coroutine and never starts the
run. Start it inside an `async def`, then drive that with `asyncio.run(main())`:

```python
run = await deck.runs.start("assistant", input="Hello!")
```

</details>

<details>
<summary>The session already has a run in flight</summary>

```text
agentdeck.core.errors.SessionBusyError: session 'quickstart' already has run '<first-run-id>' in
flight, so run '<second-run-id>' cannot start on it
```

Sessions serialize turns, and a second run started on the same `session_id` before the first
reached a terminal event. Wait for the first with `result = await run`, cancel it with
`await run.cancel()`, or give the independent conversation its own `session_id`. Full rules:
[sessions](/runs-and-control/sessions).

</details>

<details>
<summary>The Deck is not open</summary>

```text
agentdeck.core.errors.ConfigError: this Deck is not open: use `async with deck:`
(or `await deck.__aenter__()`) first.
```

`deck.stream(...)` was iterated before the runtime opened. Wrap it in the Deck context:

```python
async with deck:
    async for event in deck.stream("assistant", "Hello!"):
        print(event.kind)
```

</details>

## Next

- [Add a Tool](/build-your-deck/tools) -> Give your agents callable capabilities.
- [Define Agents](/build-your-deck/agents) -> Build decision-making LLM agents.
- [Workflows](/build-your-deck/workflows) -> Build multi-step deterministic graphs.
- [Understand Runs & Control](/runs-and-control/runs) -> Learn lifecycle, streaming, and inspection.
- [Bring an Existing Agent](/integrations/existing-agents) -> Wrap OpenAI Agents SDK agents into a Deck.
- [API Reference](/reference/deck) -> Full reference for Deck and Run.
