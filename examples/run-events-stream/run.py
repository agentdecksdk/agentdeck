import asyncio

from agentdeck import Agent, Deck
from agentdeck.testing import ScriptedModel, patch_model


async def main() -> None:
    deck = Deck(agents=[Agent(name="Greeter", instructions="Answer with a short greeting.")])
    # `patch_model` swaps the SDK boundary for a scripted reply, so the stream below is the real
    # Runtime's event log and reaches no model. It is what lets the README print one fixed output.
    with patch_model(ScriptedModel(deltas=("Hel", "lo"))):
        async with deck:
            async for event in deck.stream("Greeter", "Say hello", session_id="demo-session"):
                print(f"{event.kind}: {event.payload.model_dump(exclude={'kind'})}")


asyncio.run(main())
