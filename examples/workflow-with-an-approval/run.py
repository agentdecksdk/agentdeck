import asyncio

from agentdeck import Deck


async def main() -> None:
    async with Deck.from_project() as deck:
        paused = await deck.run("RefundApproval", {"order_id": "A-1003"}, session_id="refund-A-1003")
        print(paused)  # {"type": "interrupt", "payload": {"question": ...}, "thread_id": ...}

        # In a real deployment the answer comes from wherever the person is — a second process
        # reaches the same run through a shared event log, not through this variable.
        [pending] = [p for p in await deck.pending() if p.thread_id == "refund-A-1003"]
        final = await deck.answer(pending.run_id, "yes")
        print(final)


asyncio.run(main())
