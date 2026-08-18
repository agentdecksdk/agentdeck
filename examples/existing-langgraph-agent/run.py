import asyncio

from agentdeck import Deck


async def main() -> None:
    async with Deck.from_project() as deck:
        # The graph runs exactly as it did standalone  -  same nodes, same final state.
        final = await deck.run("Triage", {"input": "the checkout API is down for everyone"})
        print(final)  # {'input': ..., 'severity': 'urgent', 'queue': 'oncall', 'reply': ...}

        # What wrapping bought: the same run, observable while it happens. One ordered log per
        # run, whatever started it  -  nothing in pipeline.py emits any of this.
        async for event in deck.stream("Triage", {"input": "how do I change my password?"}):
            print(event.kind)  # run.started … node.updated … run.completed


asyncio.run(main())
