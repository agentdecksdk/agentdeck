import asyncio

from agentdeck import Deck
from agentdeck.core import RunStatus


async def main() -> None:
    async with Deck.from_project() as deck:
        paused = await deck.run("RefundApproval", {"order_id": "A-1003"}, session_id="refund-A-1003")
        print(paused)  # {"type": "interrupt", "payload": {"question": ...}, "id": ...}

        # In a real deployment the answer comes from wherever the person is: a second process
        # reaches the same run through a shared event log, not through this variable. The inbox
        # is a list of runs, and each one answers itself.
        [run] = await deck.runs.list(status=RunStatus.WAITING_ANSWER)
        await run.answer("yes")
        print(await run)


asyncio.run(main())
