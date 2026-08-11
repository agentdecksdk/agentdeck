import asyncio

from agentdeck import Deck


async def main() -> None:
    async with Deck.from_project() as deck:
        result = await deck.run("OrderDesk", "where is order A-1001?")
        print(result.output)


asyncio.run(main())
